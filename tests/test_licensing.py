import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scleaner.licensing import (LicenseConfig, LicenseError, LicenseManager, canonical, encode64,
                                read_document, verify_license)
from scleaner.storage import Storage
from scleaner_server.signing import issue_license


class LicenseTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.fixtures)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.config = LicenseConfig("test", {"test-key": self.private.public_key().public_bytes_raw()})
        self.manager = LicenseManager(Storage(self.root), self.config)
        self.device = self.manager.device_id()
        self.now = int(time.time())
        self.document = issue_license(self.private, "test-key", self.device, issued_at=self.now)

    def tearDown(self):
        self.temp.cleanup()

    def resign(self, document):
        document["signature"] = encode64(self.private.sign(canonical(document["claims"])))
        return document

    def test_valid_signature_persists_and_works_offline_after_restart(self):
        Storage(self.root).save_settings({"age_hours": 72})
        state = self.manager.install(json.dumps(self.document).encode())
        self.assertTrue(state.is_pro)
        reloaded = LicenseManager(Storage(self.root), self.config)
        self.assertEqual(reloaded.state().license_id, state.license_id)
        self.assertEqual(reloaded.require("pro_preview"), state)
        self.assertEqual(Storage(self.root).read_settings()["age_hours"], 72)

    def test_tampering_unknown_key_and_wrong_installation_are_rejected(self):
        for field, value in (("expires_at", self.now + 9999999), ("edition", "free"), ("key_id", "unknown")):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.document)
                changed["claims"][field] = value
                with self.assertRaises(LicenseError):
                    verify_license(changed, self.config, self.device)
        with self.assertRaises(LicenseError):
            verify_license(self.document, self.config, str(uuid.uuid4()))

    def test_signed_but_invalid_entitlements_are_rejected(self):
        cases = {"schema": True, "edition": "free", "product": "different", "environment": "production",
                 "major_version": 99, "features": ["erase_anything"], "issued_at": self.now + 1000,
                 "expires_at": None, "license_id": [], "device_id": str(uuid.uuid4())}
        for field, value in cases.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(self.document)
                changed["claims"][field] = value
                with self.assertRaises(LicenseError):
                    verify_license(self.resign(changed), self.config, self.device)

    def test_expiration_is_checked_at_every_feature_entry(self):
        self.manager.install(json.dumps(self.document).encode())
        self.assertFalse(self.manager.state(now=self.now + 7 * 86400).is_pro)
        with patch("scleaner.licensing.time.time", return_value=self.now + 7 * 86400):
            with self.assertRaises(LicenseError):
                self.manager.require("pro_preview")
        with self.assertRaises(LicenseError):
            self.manager.require("unknown")

    def test_production_does_not_accept_test_license_or_test_storage(self):
        self.manager.install(json.dumps(self.document).encode())
        normal = LicenseManager(Storage(self.root))
        self.assertFalse(normal.state().is_pro)
        with self.assertRaises(LicenseError):
            normal.install(json.dumps(self.document).encode())
        with self.assertRaises(LicenseError):
            verify_license(self.document, LicenseConfig("production", self.config.public_keys), self.device)

    def test_invalid_import_and_failed_write_preserve_existing_license(self):
        self.manager.install(json.dumps(self.document).encode())
        original = self.manager.path.read_bytes()
        with self.assertRaises(LicenseError):
            self.manager.install(b'{"pro":true}')
        with patch.object(self.manager.storage, "write_json", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                self.manager.install(json.dumps(self.document).encode())
        self.assertEqual(self.manager.path.read_bytes(), original)

    def test_corruption_and_boolean_setting_cannot_enable_pro(self):
        Storage(self.root).save_settings({"is_pro": True})
        self.assertFalse(self.manager.state().is_pro)
        self.manager.path.write_text("broken", encoding="utf-8")
        self.assertFalse(self.manager.state().is_pro)
        self.assertTrue(self.manager.state().message)
        for raw in (b"[]", b'{"claims":{},"claims":{}}', b"x" * 16385):
            with self.assertRaises(LicenseError):
                read_document(raw)

    def test_free_startup_does_not_create_license_files(self):
        manager = LicenseManager(Storage(self.root / "unused"))
        self.assertFalse(manager.state().is_pro)
        self.assertFalse(manager.storage.root.exists())

    def test_corrupt_installation_never_silently_changes_binding(self):
        self.manager.install(json.dumps(self.document).encode())
        path = self.manager.storage.root / "installation.json"
        path.write_text('{"id": "broken"}')
        self.assertFalse(self.manager.state().is_pro)
        with self.assertRaises(LicenseError):
            self.manager.device_id()
        self.assertEqual(json.loads(path.read_text())["id"], "broken")

    def test_config_rejects_untrusted_transport_and_requires_explicit_test_mode(self):
        path = self.root / "config.json"
        base = {"environment": "test", "public_keys": {"test-key": encode64(self.private.public_key().public_bytes_raw())}}
        for url in ("http://example.com", "file:///tmp/key", "https://example.com@evil.com/path", "https://example.com?secret=1"):
            path.write_text(json.dumps({**base, "server_url": url}))
            with self.assertRaises(LicenseError):
                LicenseConfig.test_file(path)
        path.write_text(json.dumps({**base, "server_url": "http://127.0.0.1:8765"}))
        self.assertEqual(LicenseConfig.test_file(path).environment, "test")


if __name__ == "__main__":
    unittest.main()
