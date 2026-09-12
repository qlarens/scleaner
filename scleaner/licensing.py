"""Offline verification of signed entitlements. Private keys never belong here."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__
from .storage import Storage

MAX_LICENSE_BYTES = 16384
PRO_FEATURES = frozenset({"pro_preview"})


class LicenseError(ValueError):
    pass


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def encode64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode64(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_document(raw: bytes) -> dict:
    if len(raw) > MAX_LICENSE_BYTES:
        raise LicenseError("Файл лицензии слишком большой.")
    try:
        result = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(result, dict):
            raise ValueError("Expected object")
        return result
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LicenseError("Не удалось прочитать файл лицензии.") from error


def read_limited(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(MAX_LICENSE_BYTES + 1)


@dataclass(frozen=True)
class LicenseConfig:
    environment: str = "production"
    public_keys: dict[str, bytes] = field(default_factory=dict)
    server_url: str = ""

    @classmethod
    def test_file(cls, path: Path):
        raw = read_document(read_limited(path))
        try:
            if raw["environment"] != "test":
                raise ValueError("Only test configuration is supported")
            keys = {key: decode64(value) for key, value in raw["public_keys"].items()}
            if not keys or any(len(value) != 32 for value in keys.values()):
                raise ValueError("Invalid public keys")
            url = raw.get("server_url", "").rstrip("/")
            if url:
                parsed = urlsplit(url)
                local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.netloc:
                    raise ValueError("HTTPS required outside loopback")
                if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
                    raise ValueError("Expected server origin")
            return cls("test", keys, url)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise LicenseError("Неверная конфигурация тестовых лицензий.") from error


@dataclass(frozen=True)
class LicenseState:
    edition: str = "free"
    environment: str = "production"
    license_id: str = ""
    expires_at: int | None = None
    features: frozenset[str] = frozenset()
    message: str = ""

    @property
    def is_pro(self):
        return self.edition == "pro"

    @property
    def title(self):
        return "Pro · тест" if self.is_pro and self.environment == "test" else "Pro" if self.is_pro else "Free"


def verify_license(document: dict, config: LicenseConfig, device_id: str, *, now: int | None = None) -> LicenseState:
    now = int(time.time()) if now is None else now
    try:
        if set(document) != {"claims", "signature"} or not isinstance(document["claims"], dict):
            raise ValueError("Invalid envelope")
        claims = document["claims"]
        key = config.public_keys.get(claims.get("key_id"))
        if key is None:
            raise LicenseError("Лицензия подписана неизвестным ключом.")
        Ed25519PublicKey.from_public_bytes(key).verify(decode64(document["signature"]), canonical(claims))
        expected = {"schema", "key_id", "product", "edition", "environment", "license_id", "device_id",
                    "issued_at", "expires_at", "major_version", "features"}
        if set(claims) != expected or type(claims["schema"]) is not int or claims["schema"] != 1:
            raise ValueError("Unsupported schema")
        if claims["product"] != "scleaner" or claims["edition"] != "pro":
            raise LicenseError("Эта лицензия не подходит для SCleaner Pro.")
        if claims["environment"] != config.environment:
            raise LicenseError("Тестовая и обычная лицензии несовместимы.")
        if claims["device_id"] != device_id:
            raise LicenseError("Лицензия активирована для другой установки SCleaner.")
        if type(claims["major_version"]) is not int or claims["major_version"] != int(__version__.split(".")[0]):
            raise LicenseError("Лицензия предназначена для другой основной версии SCleaner.")
        issued, expires = claims["issued_at"], claims["expires_at"]
        if type(issued) is not int or issued < 0 or issued > now + 300:
            raise LicenseError("Проверьте дату на компьютере: лицензия ещё не действует.")
        if expires is not None and (type(expires) is not int or not issued < expires <= 253402214400):
            raise ValueError("Invalid expiration")
        if config.environment == "test" and expires is None:
            raise ValueError("Test licenses must expire")
        if expires is not None and now >= expires:
            raise LicenseError("Срок тестовой лицензии истёк. Можно активировать новую.")
        features = claims["features"]
        if not isinstance(features, list) or any(not isinstance(f, str) or f not in PRO_FEATURES for f in features):
            raise ValueError("Unknown feature")
        if not isinstance(claims["license_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", claims["license_id"]):
            raise ValueError("Invalid license ID")
        return LicenseState("pro", config.environment, claims["license_id"], expires, frozenset(features))
    except LicenseError:
        raise
    except InvalidSignature as error:
        raise LicenseError("Подпись лицензии недействительна. Файл мог быть изменён.") from error
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise LicenseError("Неверный формат лицензии.") from error


class LicenseManager:
    def __init__(self, storage: Storage, config: LicenseConfig | None = None):
        self.config = config or LicenseConfig()
        self.storage = Storage(storage.root / ("licensing-test" if self.config.environment == "test" else "licensing"))
        self.path = self.storage.root / "license.json"

    def device_id(self) -> str:
        path = self.storage.root / "installation.json"
        if path.exists():
            try:
                value = read_document(read_limited(path))["id"]
                return str(uuid.UUID(value))
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise LicenseError("Не удалось прочитать идентификатор установки.") from error
        value = str(uuid.uuid4())
        self.storage.write_json(path, {"id": value})
        return value

    def state(self, *, now: int | None = None) -> LicenseState:
        try:
            if self.path.exists():
                return verify_license(read_document(read_limited(self.path)), self.config, self.device_id(), now=now)
            return LicenseState(environment=self.config.environment)
        except (OSError, LicenseError) as error:
            return LicenseState(environment=self.config.environment, message=str(error))

    def install(self, raw: bytes) -> LicenseState:
        document = read_document(raw)
        state = verify_license(document, self.config, self.device_id())
        self.storage.write_json(self.path, document)
        return state

    def require(self, feature: str):
        state = self.state()
        if feature not in PRO_FEATURES or not state.is_pro or feature not in state.features:
            raise LicenseError("Для этой возможности требуется действующая лицензия Pro.")
        return state
