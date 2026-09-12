from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scleaner.activation import ActivationClient
from scleaner.licensing import LicenseConfig, LicenseError, LicenseManager
from scleaner.storage import Storage
from scleaner_server.payments import StripeTestPayments
from scleaner_server.server import SandboxHTTPServer, SandboxService


class ActivationTest(unittest.TestCase):
    def setUp(self):
        fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=fixtures)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.service = SandboxService(self.root / "orders.sqlite3", self.private)
        self.server = SandboxHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = LicenseConfig("test", {"local-test": self.private.public_key().public_bytes_raw()}, self.server.origin)
        self.manager = LicenseManager(Storage(self.root / "app"), self.config)
        self.client = ActivationClient(self.manager)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def buy_mock(self):
        url = self.client.checkout()
        token = url.rsplit("/", 1)[-1]
        row = self.service.order(token)
        data = urlencode({"csrf": row["csrf"], "action": "pay"}).encode()
        with urlopen(Request(url, data=data), timeout=5) as response:
            self.assertIn("Тестовая покупка подтверждена", response.read().decode())
        return token

    def test_complete_checkout_activate_restart_and_offline_flow(self):
        token = self.buy_mock()
        key = self.client.check_purchase()
        state = self.manager.state()
        self.assertTrue(state.is_pro)
        self.assertTrue(key.startswith("SCT-"))
        reloaded = LicenseManager(Storage(self.root / "app"), self.config)
        with patch("scleaner.activation.build_opener", side_effect=OSError("offline")):
            self.assertEqual(reloaded.state().license_id, state.license_id)
        self.assertEqual(self.client.activate(key).license_id, state.license_id)
        self.assertEqual(self.service.status(token)["key"], key)
        with self.assertRaises(LicenseError):
            self.service.activate(key, str(uuid.uuid4()))

    def test_pending_cancelled_and_invalid_keys_do_not_grant_pro(self):
        url = self.client.checkout()
        token = url.rsplit("/", 1)[-1]
        with self.assertRaises(LicenseError):
            self.client.check_purchase()
        row = self.service.order(token)
        self.service.mock_payment(token, row["csrf"], "cancel")
        self.assertIsNone(self.service.status(token)["key"])
        for key in ("invalid", "SCT-" + "x" * 32):
            with self.assertRaises(LicenseError):
                self.client.activate(key)
        self.assertFalse(self.manager.state().is_pro)

    def test_get_and_cross_site_requests_cannot_confirm_payment(self):
        url = self.client.checkout()
        token = url.rsplit("/", 1)[-1]
        with urlopen(url, timeout=5) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.service.status(token)["status"], "pending")
        with self.assertRaises(LicenseError):
            self.service.mock_payment(token, "wrong", "pay")
        request = Request(self.server.origin + "/v1/checkouts", data=b"{}",
                          headers={"Content-Type": "application/json", "Origin": "https://untrusted.example"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 400)

    def test_duplicate_payments_and_simultaneous_activations_are_idempotent(self):
        token = self.buy_mock()
        original = self.service.status(token)["key"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.service.fulfill(token, event_id="evt_once"), range(4)))
        self.assertEqual(self.service.status(token)["key"], original)
        devices = [str(uuid.uuid4()), str(uuid.uuid4())]
        def attempt(device):
            try:
                self.service.activate(original, device)
                return True
            except LicenseError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, devices)), 1)
        reopened = SandboxService(self.root / "orders.sqlite3", self.private)
        self.assertEqual(reopened.status(token)["key"], original)

    def test_bad_server_responses_and_redirects_are_not_trusted(self):
        with patch.object(self.client, "request", return_value={"order_token": "a" * 32, "checkout_url": "https://evil.example/pay"}):
            with self.assertRaises(LicenseError):
                self.client.checkout()
        with patch.object(self.client, "request", return_value={"license": {"claims": {}, "signature": "fake"}}):
            with self.assertRaises(LicenseError):
                self.client.activate("SCT-" + "a" * 32)
        self.assertFalse(self.manager.state().is_pro)

    def test_stripe_webhook_requires_matching_paid_session_and_is_repeatable(self):
        provider = StripeTestPayments("sk_test_placeholder", "whsec_placeholder", "price_placeholder")
        self.service.payments = provider
        with patch.object(provider, "create", return_value={"id": "cs_test_order", "url": "https://checkout.stripe.com/c/test"}):
            self.client.checkout()
        pending = json.loads((self.manager.storage.root / "pending-order.json").read_text())
        token = pending["order_token"]
        event = {"id": "evt_order", "livemode": False, "type": "checkout.session.completed",
                 "data": {"object": {"id": "cs_test_order"}}}
        def deliver(value):
            raw = json.dumps(value).encode()
            stamp = str(int(time.time()))
            signature = hmac.new(b"whsec_placeholder", stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
            return self.service.webhook(raw, f"t={stamp},v1={signature}")
        session = {"id": "cs_test_order", "livemode": False, "mode": "payment", "payment_status": "unpaid",
                   "metadata": {"order_token": token}}
        with patch.object(provider, "paid_session", return_value=session):
            deliver(event)
        self.assertIsNone(self.service.status(token)["key"])
        session["payment_status"] = "paid"
        for invalid in ({**session, "id": "cs_test_other"}, {**session, "livemode": True}, {**session, "mode": "subscription"}):
            with patch.object(provider, "paid_session", return_value=invalid), self.assertRaises(LicenseError):
                deliver(event)
        with patch.object(provider, "paid_session", return_value=session):
            event["type"] = "checkout.session.async_payment_succeeded"
            deliver(event)
            key = self.service.status(token)["key"]
            deliver(event)
            deliver({**event, "id": "evt_second"})
        self.assertEqual(self.service.status(token)["key"], key)
        self.assertEqual(self.client.check_purchase(), key)
        with self.assertRaises(LicenseError):
            self.service.mock_payment(token, self.service.order(token)["csrf"], "pay")


class StripeTest(unittest.TestCase):
    def setUp(self):
        self.provider = StripeTestPayments("sk_test_placeholder", "whsec_placeholder", "price_placeholder")

    def sign(self, raw, timestamp):
        signature = hmac.new(b"whsec_placeholder", str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={signature}"

    def test_live_credentials_bad_signatures_stale_and_live_events_are_rejected(self):
        with self.assertRaises(ValueError):
            StripeTestPayments("sk_live_placeholder", "whsec_placeholder", "price_placeholder")
        now = int(time.time())
        raw = json.dumps({"id": "evt_test", "livemode": False}).encode()
        self.assertEqual(self.provider.event(raw, self.sign(raw, now))["id"], "evt_test")
        for signature in ("", self.sign(raw, now - 301), self.sign(raw + b"x", now)):
            with self.assertRaises(LicenseError):
                self.provider.event(raw, signature, now=now)
        raw = json.dumps({"id": "evt_live", "livemode": True}).encode()
        with self.assertRaises(LicenseError):
            self.provider.event(raw, self.sign(raw, now))

    def test_checkout_amount_is_server_controlled_and_only_test_session_accepted(self):
        with patch.object(self.provider, "api", return_value={"id": "cs_test_123", "url": "https://checkout.stripe.com/c/test", "livemode": False}) as api:
            self.provider.create("order123", "http://127.0.0.1:8765")
            values = api.call_args.args[1]
            self.assertEqual(values["line_items[0][price]"], "price_placeholder")
            self.assertEqual(values["line_items[0][quantity]"], "1")
            self.assertEqual(values["mode"], "payment")
            self.assertEqual(api.call_args.kwargs["idempotency_key"], "scleaner-test-order123")


if __name__ == "__main__":
    unittest.main()
