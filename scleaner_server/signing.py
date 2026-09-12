"""Issuer-side signing; only public verification code is shipped in the app."""
import time
import uuid

from scleaner import __version__
from scleaner.licensing import canonical, encode64


def issue_license(private_key, key_id, device_id, *, license_id=None, issued_at=None, expires_at=None):
    issued_at = int(time.time()) if issued_at is None else issued_at
    claims = {
        "schema": 1, "key_id": key_id, "product": "scleaner", "edition": "pro", "environment": "test",
        "license_id": license_id or uuid.uuid4().hex, "device_id": str(uuid.UUID(device_id)),
        "issued_at": issued_at, "expires_at": expires_at if expires_at is not None else issued_at + 7 * 86400,
        "major_version": int(__version__.split(".")[0]), "features": ["pro_preview"],
    }
    return {"claims": claims, "signature": encode64(private_key.sign(canonical(claims)))}
