"""Stripe TEST adapter. No live payment credentials are accepted."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from scleaner.activation import NoRedirects
from scleaner.licensing import LicenseError


class StripeTestPayments:
    def __init__(self, secret_key, webhook_secret, price_id):
        if not secret_key.startswith("sk_test_") or not webhook_secret.startswith("whsec_") or not price_id.startswith("price_"):
            raise ValueError("Stripe requires sk_test_, whsec_ and a test price_ ID. Live keys are refused.")
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret
        self.price_id = price_id

    def api(self, path, data=None, *, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.secret_key}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        body = None
        if data is not None:
            body = urlencode(data).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request("https://api.stripe.com/v1/checkout/sessions" + path, data=body, headers=headers)
        try:
            with build_opener(NoRedirects()).open(request, timeout=10) as response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError("Oversized response")
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get("livemode") is not False:
                    raise ValueError("Only Stripe test sessions are allowed")
                return result
        except (OSError, ValueError) as error:
            raise LicenseError("Stripe не ответил. Проверьте тестовую конфигурацию сервера и повторите попытку.") from error

    def create(self, order_token, origin):
        session = self.api("", {
            "mode": "payment", "line_items[0][price]": self.price_id, "line_items[0][quantity]": "1",
            "metadata[order_token]": order_token,
            "success_url": origin + "/checkout/" + order_token,
            "cancel_url": origin + "/checkout/" + order_token,
        }, idempotency_key="scleaner-test-" + order_token)
        if not isinstance(session.get("id"), str) or not session["id"].startswith("cs_test_"):
            raise LicenseError("Stripe вернул неверную тестовую сессию.")
        return session

    def event(self, raw, signature, *, now=None):
        try:
            fields = [part.split("=", 1) for part in signature.split(",")]
            stamp = next(value for name, value in fields if name == "t")
            signatures = [value for name, value in fields if name == "v1"]
            if abs((time.time() if now is None else now) - int(stamp)) > 300:
                raise ValueError("Stale event")
            expected = hmac.new(self.webhook_secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
            if not any(hmac.compare_digest(expected, value) for value in signatures):
                raise ValueError("Invalid signature")
            event = json.loads(raw)
            if event.get("livemode") is not False or not isinstance(event.get("id"), str):
                raise ValueError("Expected test event")
            return event
        except (ValueError, TypeError, AttributeError, StopIteration) as error:
            raise LicenseError("Неверное подтверждение тестового платежа.") from error

    def paid_session(self, session_id):
        if not isinstance(session_id, str) or not session_id.startswith("cs_test_") or not session_id.replace("_", "").isalnum():
            raise LicenseError("Неверная тестовая сессия.")
        return self.api("/" + session_id)
