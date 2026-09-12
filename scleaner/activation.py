"""Explicit, bounded network requests; normal startup makes no network calls."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .licensing import LicenseError, LicenseManager, MAX_LICENSE_BYTES, read_document


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ActivationClient:
    def __init__(self, manager: LicenseManager):
        self.manager = manager
        self.base_url = manager.config.server_url

    def request(self, path: str, payload: dict) -> dict:
        if not self.base_url:
            raise LicenseError("Сервис активации пока не подключён. Можно импортировать файл лицензии.")
        request = Request(self.base_url + path, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        handlers = [NoRedirects()]
        if urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(ProxyHandler({}))
        try:
            with build_opener(*handlers).open(request, timeout=10) as response:
                return read_document(response.read(MAX_LICENSE_BYTES + 1))
        except HTTPError as error:
            # Display only this protocol's error text; never echo payment provider responses.
            try:
                message = read_document(error.read(MAX_LICENSE_BYTES + 1)).get("error")
                if isinstance(message, str) and len(message) <= 300:
                    raise LicenseError(message) from error
            finally:
                error.close()
            raise LicenseError("Сервис не смог обработать запрос. Попробуйте ещё раз.") from error
        except (URLError, OSError, TimeoutError) as error:
            raise LicenseError("Не удалось связаться с сервисом. Проверьте подключение и повторите попытку.") from error

    def activate(self, key: str):
        key = key.strip()
        if not re.fullmatch(r"SCT-[A-Za-z0-9_-]{20,80}", key):
            raise LicenseError("Проверьте ключ: тестовый ключ начинается с SCT-.")
        result = self.request("/v1/activate", {"key": key, "device_id": self.manager.device_id()})
        if not isinstance(result.get("license"), dict):
            raise LicenseError("Сервис не вернул лицензию.")
        return self.manager.install(json.dumps(result["license"]).encode("utf-8"))

    def checkout(self):
        result = self.request("/v1/checkouts", {})
        token, url = result.get("order_token"), result.get("checkout_url")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,80}", token) or not isinstance(url, str):
            raise LicenseError("Сервис вернул неверные данные покупки.")
        parsed = urlsplit(url)
        base = urlsplit(self.base_url)
        own = (parsed.scheme, parsed.netloc) == (base.scheme, base.netloc) and parsed.path == "/checkout/" + token
        stripe = parsed.scheme == "https" and parsed.netloc == "checkout.stripe.com"
        if parsed.username or parsed.password or not (own or stripe):
            raise LicenseError("Сервис вернул недопустимый адрес оплаты.")
        self.manager.storage.write_json(self.manager.storage.root / "pending-order.json", {"order_token": token})
        return url

    def check_purchase(self):
        from .licensing import read_limited
        path = self.manager.storage.root / "pending-order.json"
        try:
            token = read_document(read_limited(path))["order_token"]
        except (OSError, KeyError, LicenseError) as error:
            raise LicenseError("Сначала создайте тестовую покупку или введите полученный ключ.") from error
        result = self.request("/v1/orders/status", {"order_token": token})
        if result.get("status") != "paid":
            raise LicenseError("Оплата пока не подтверждена. Завершите покупку в браузере и проверьте ещё раз.")
        key = result.get("key")
        if not isinstance(key, str):
            raise LicenseError("Сервис не вернул ключ покупки.")
        self.activate(key)
        return key
