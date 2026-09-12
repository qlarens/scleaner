"""Loopback-only sandbox server with persistent orders and signed test licenses."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scleaner.licensing import LicenseError, read_document
from .payments import StripeTestPayments
from .signing import issue_license


class SandboxService:
    def __init__(self, database: Path, private_key, key_id="local-test", payments=None):
        self.database = database
        self.private_key = private_key
        self.key_id = key_id
        self.payments = payments
        database.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    token TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL,
                    csrf TEXT NOT NULL, session_id TEXT UNIQUE, license_key TEXT UNIQUE,
                    license_id TEXT, device_id TEXT, paid_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY);
            """)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def order(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,80}", token):
            raise LicenseError("Покупка не найдена.")
        with self.db() as conn:
            row = conn.execute("SELECT * FROM orders WHERE token = ?", (token,)).fetchone()
        if row is None:
            raise LicenseError("Покупка не найдена.")
        return row

    def create_checkout(self, origin):
        token = secrets.token_urlsafe(32)
        provider = "stripe-test" if self.payments else "mock"
        with self.db() as conn:
            conn.execute("INSERT INTO orders(token, provider, status, csrf) VALUES (?, ?, 'pending', ?)",
                         (token, provider, secrets.token_urlsafe(32)))
        url = origin + "/checkout/" + token
        if self.payments:
            session = self.payments.create(token, origin)
            url = session.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme != "https" or urlsplit(url).netloc != "checkout.stripe.com":
                raise LicenseError("Stripe вернул неверный адрес оплаты.")
            with self.db() as conn:
                conn.execute("UPDATE orders SET session_id = ? WHERE token = ?", (session["id"], token))
        return {"order_token": token, "checkout_url": url}

    def fulfill(self, token, *, event_id=None):
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event_id and conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
                return
            row = conn.execute("SELECT * FROM orders WHERE token = ?", (token,)).fetchone()
            if row is None:
                raise LicenseError("Покупка не найдена.")
            if row["status"] == "pending":
                conn.execute("UPDATE orders SET status = 'paid', license_key = ?, license_id = ?, paid_at = ? WHERE token = ?",
                             ("SCT-" + secrets.token_urlsafe(24), uuid.uuid4().hex, int(time.time()), token))
            if event_id:
                conn.execute("INSERT INTO events(id) VALUES (?)", (event_id,))

    def mock_payment(self, token, csrf, action):
        row = self.order(token)
        if self.payments or row["provider"] != "mock" or not isinstance(csrf, str) or not secrets.compare_digest(row["csrf"], csrf):
            raise LicenseError("Недопустимый запрос тестовой оплаты.")
        if action == "pay":
            self.fulfill(token)
        elif action != "cancel":
            raise LicenseError("Неизвестное действие.")
        # Cancelling leaves the order unpaid and retryable; it never issues a key.

    def status(self, token):
        row = self.order(token)
        return {"status": row["status"], "key": row["license_key"] if row["status"] == "paid" else None}

    def activate(self, key, device_id):
        if not isinstance(key, str) or not re.fullmatch(r"SCT-[A-Za-z0-9_-]{20,80}", key):
            raise LicenseError("Ключ не найден или покупка не подтверждена.")
        try:
            device_id = str(uuid.UUID(device_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise LicenseError("Неверный код установки.") from error
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE license_key = ? AND status = 'paid'", (key,)).fetchone()
            if row is None:
                raise LicenseError("Ключ не найден или покупка не подтверждена.")
            if row["device_id"] and row["device_id"] != device_id:
                raise LicenseError("Ключ уже активирован для другой установки. Для теста создайте новую покупку.")
            if int(time.time()) >= row["paid_at"] + 7 * 86400:
                raise LicenseError("Срок тестовой лицензии истёк. Создайте новую тестовую покупку.")
            conn.execute("UPDATE orders SET device_id = ? WHERE token = ?", (device_id, row["token"]))
            document = issue_license(self.private_key, self.key_id, device_id, license_id=row["license_id"],
                                     issued_at=row["paid_at"], expires_at=row["paid_at"] + 7 * 86400)
        return {"license": document}

    def webhook(self, raw, signature):
        if not self.payments:
            raise LicenseError("Stripe не подключён.")
        event = self.payments.event(raw, signature)
        if event.get("type") not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            return {"received": True}
        try:
            session_id = event["data"]["object"]["id"]
            session = self.payments.paid_session(session_id)
            token = session["metadata"]["order_token"]
            row = self.order(token)
            if row["provider"] != "stripe-test" or row["session_id"] != session_id or session.get("id") != session_id:
                raise LicenseError("Платёж не соответствует покупке.")
            if session.get("mode") != "payment" or session.get("livemode") is not False:
                raise LicenseError("Ожидалась тестовая разовая покупка.")
            if session.get("payment_status") == "paid":
                self.fulfill(token, event_id=event["id"])
        except (KeyError, TypeError) as error:
            raise LicenseError("Неверное подтверждение покупки.") from error
        return {"received": True}


def checkout_page(row):
    token, csrf = escape(row["token"]), escape(row["csrf"])
    if row["status"] == "paid":
        body = '<h1>Тестовая покупка подтверждена</h1><p>Ваш тестовый ключ:</p><code>' + escape(row["license_key"]) + '</code><p>Вернитесь в SCleaner и нажмите «Проверить покупку» или вставьте ключ в поле активации.</p>'
    elif row["provider"] == "mock":
        body = f'''<h1>SCleaner Pro</h1><p>Локальная проверка покупки и активации. Банковская карта не нужна.</p>
        <p>Вы получите тестовую лицензию на 7 дней. Новые функции Pro ещё в разработке.</p>
        <form method="post" action="/checkout/{token}"><input type="hidden" name="csrf" value="{csrf}">
        <button name="action" value="pay">Подтвердить тестовую покупку</button>
        <button class="secondary" name="action" value="cancel">Отменить</button></form>'''
    else:
        body = '<h1>Проверяем оплату</h1><p>Вернитесь в SCleaner и нажмите «Проверить покупку». Если вы отменили оплату, лицензия не будет выдана.</p><p>Подтверждение может поступить с задержкой.</p>'
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>SCleaner · Тестовая покупка</title><style>
    body{margin:0;background:#131620;color:#e9ecf5;font:16px/1.6 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}
    main{max-width:620px;margin:24px;padding:36px;background:#1b1f2b;border:1px solid #343b4e;border-radius:18px}
    .badge{color:#86e3c3;font-size:14px}h1{font-size:32px;line-height:1.2}p{color:#b8c0d2}
    button{font:inherit;background:#9584ff;color:#141126;border:0;border-radius:8px;padding:12px 18px;cursor:pointer;margin:8px 8px 0 0}
    .secondary{background:#30374a;color:#e9ecf5}code{display:block;overflow-wrap:anywhere;background:#131620;padding:16px;border-radius:8px}
    </style><main><div class="badge">ТЕСТОВЫЙ РЕЖИМ · БЕЗ СПИСАНИЙ</div>''' + body + '</main></html>'


class SandboxHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service):
        if address[0] != "127.0.0.1":
            raise ValueError("Sandbox server must bind to 127.0.0.1")
        super().__init__(address, SandboxHandler)
        self.service = service
        self.origin = f"http://127.0.0.1:{self.server_port}"


class SandboxHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # URLs contain order capabilities; never log them.

    def reply(self, status, value, *, html=False):
        raw = value.encode("utf-8") if html else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def validate_origin(self):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            raise LicenseError("Недопустимый адрес сервера.")
        if self.headers.get("Origin") not in {None, self.server.origin}:
            raise LicenseError("Недопустимый источник запроса.")

    def do_GET(self):
        try:
            self.validate_origin()
            if self.path == "/health":
                return self.reply(200, {"environment": "test", "provider": "stripe-test" if self.server.service.payments else "mock"})
            if self.path.startswith("/checkout/"):
                row = self.server.service.order(self.path.removeprefix("/checkout/"))
                return self.reply(200, checkout_page(row), html=True)
            self.reply(404, {"error": "Страница не найдена."})
        except LicenseError as error:
            self.reply(400, {"error": str(error)})

    def do_POST(self):
        try:
            self.validate_origin()
            if self.headers.get("Transfer-Encoding"):
                raise LicenseError("Недопустимое кодирование запроса.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (262144 if self.path == "/v1/stripe/webhook" else 16384):
                raise LicenseError("Недопустимый размер запроса.")
            self.connection.settimeout(12)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise LicenseError("Неполный запрос.")
            service = self.server.service
            if self.path.startswith("/checkout/"):
                if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    raise LicenseError("Ожидалась форма тестовой оплаты.")
                fields = parse_qs(raw.decode("utf-8"), max_num_fields=4)
                token = self.path.removeprefix("/checkout/")
                action = fields.get("action", [""])[0]
                service.mock_payment(token, fields.get("csrf", [""])[0], action)
                if action == "cancel":
                    return self.reply(200, '<!doctype html><meta charset="utf-8"><title>SCleaner</title><p>Тестовая покупка отменена. Лицензия не выдана. Можно закрыть эту вкладку.</p>', html=True)
                return self.reply(200, checkout_page(service.order(token)), html=True)
            if self.headers.get_content_type() != "application/json":
                raise LicenseError("Ожидался JSON-запрос.")
            if self.path == "/v1/stripe/webhook":
                return self.reply(200, service.webhook(raw, self.headers.get("Stripe-Signature", "")))
            payload = read_document(raw)
            if self.path == "/v1/checkouts":
                result = service.create_checkout(self.server.origin)
            elif self.path == "/v1/orders/status":
                result = service.status(payload.get("order_token"))
            elif self.path == "/v1/activate":
                result = service.activate(payload.get("key"), payload.get("device_id"))
            else:
                return self.reply(404, {"error": "Метод не найден."})
            self.reply(200, result)
        except (LicenseError, ValueError, UnicodeError) as error:
            message = str(error) if isinstance(error, LicenseError) else "Неверный запрос."
            self.reply(400, {"error": message})
        except (OSError, sqlite3.Error):
            self.reply(503, {"error": "Сервис временно недоступен. Повторите попытку."})


def main():
    parser = argparse.ArgumentParser(description="SCleaner loopback TEST activation service; never for production")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--provider", choices=["mock", "stripe-test"], default="mock")
    args = parser.parse_args()
    key = load_pem_private_key((args.data_dir / "issuer-private.pem").read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("Expected an Ed25519 private key")
    payments = None
    if args.provider == "stripe-test":
        try:
            payments = StripeTestPayments(os.environ.get("SCLEANER_STRIPE_SECRET_KEY", ""),
                                          os.environ.get("SCLEANER_STRIPE_WEBHOOK_SECRET", ""),
                                          os.environ.get("SCLEANER_STRIPE_PRICE_ID", ""))
        except ValueError as error:
            parser.error(str(error))
    service = SandboxService(args.data_dir / "orders.sqlite3", key, payments=payments)
    with SandboxHTTPServer(("127.0.0.1", args.port), service) as server:
        print(f"SCleaner TEST server: {server.origin} ({args.provider}). No live payments.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
