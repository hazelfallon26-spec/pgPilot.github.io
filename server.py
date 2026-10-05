"""Web server (Python standard library only - nothing extra to install). Serves the API and the tenant app.
Run:  python3 server.py create-owner      (one time: makes your PG and owner login)
      python3 server.py run               (starts the app; also runs reminders + daily backup)"""
import json, os, re, sqlite3, sys, threading, time, logging, getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import core, dashboard, notifications, notices, leave_guests, complaints, payments, tenants, assistant, setup
from core import AuthError, get_session, OWNER_SESSION_SECS, TENANT_SESSION_SECS
from dashboard import inr
from tenants import TenantError
log = logging.getLogger("pgapp"); HERE = os.path.dirname(os.path.abspath(__file__)); STATIC = os.path.join(HERE, "static")
FILES = {"tenant.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8"}
MAX_BODY = 20_000; CLASSIFIER = None     # set CLASSIFIER = complaints.make_llm_classifier(your_llm_call) once an AI service is connected
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"

def open_db(path):
    c = sqlite3.connect(path, timeout=10); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def init_db(path):
    c = dashboard.connect(path); c.execute("PRAGMA journal_mode=WAL"); c.close()

def money(p): return {"paise": p, "text": inr(p)}
def _rent(cur): return cur and {"amount": money(cur["amount_paise"]), "paid": money(cur["paid_paise"]), "remaining": money(cur["remaining_paise"]), "due_date": cur["due_date"], "due_text": leave_guests._fmt(cur["due_date"])}

# ---------------- handlers: (conn, session, body, regex_match) -> dict | (status, dict, extra_headers) ----------------
def login_tenant(c, s, b, m): tok = core.login_tenant(c, b.get("phone", ""), str(b.get("pin", ""))); return 200, {"ok": True}, _cookie(tok, TENANT_SESSION_SECS)
def login_owner(c, s, b, m): tok = core.login_owner(c, b.get("email", ""), b.get("password", "")); return 200, {"ok": True}, _cookie(tok, OWNER_SESSION_SECS)
def join(c, s, b, m): tok = core.accept_invite(c, b.get("token", ""), str(b.get("pin", ""))); return 200, {"ok": True}, _cookie(tok, TENANT_SESSION_SECS)
def invite_info(c, s, b, m):
    r = c.execute("SELECT i.name, p.name pg FROM invites i JOIN pgs p ON p.id=i.pg_id WHERE i.token_hash=? AND i.used_at IS NULL AND i.expires>?", (core._th(m.group(1)), time.time())).fetchone()
    if not r: raise AuthError("Invite is invalid or expired")
    return {"name": r["name"], "pg": r["pg"]}
def me(c, s, b, m): return {"role": s.role, "name": c.execute("SELECT name FROM users WHERE id=?", (s.user_id,)).fetchone()[0]}
def logout(c, s, b, m): return 200, {"ok": True}, [("Set-Cookie", "sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")]

def t_home(c, s, b, m):
    p, pay, cs, nt = tenants.my_profile(c, s), payments.my_payments(c, s), complaints.my_complaints(c, s), notifications.my_notifications(c, s)
    return {"name": p["name"], "pg": c.execute("SELECT name FROM pgs WHERE id=?", (s.pg_id,)).fetchone()[0], "room": p["room"], "bed": p["bed"], "floor": p["floor"], "rent": _rent(pay["current"]),
            "open_complaints": sum(x["status"] != "Confirmed" for x in cs), "needs_confirmation": sum(x["needs_my_confirmation"] for x in cs), "unread": sum(n["read_at"] is None for n in nt)}
def t_rent(c, s, b, m):
    p = payments.my_payments(c, s); d = p["deposit"]
    return {"rent": _rent(p["current"]), "total_pending": money(p["total_pending_paise"]), "advance_credit": money(p["advance_credit_paise"]),
            "history": [{"date": h["date"], "amount": money(h["amount_paise"]), "type": h["type"], "method": h["method"]} for h in p["history"]],
            "deposit": {"expected": money(d["expected_paise"]), "paid": money(d["paid_paise"]), "status": d["status"]}}
def t_complaint_new(c, s, b, m):
    cid = complaints.create_complaint(c, s, b.get("text", ""), classifier=CLASSIFIER, idem_key=b.get("idem_key"), hint=b.get("hint"))
    return {"id": cid, "status": next((x["status"] for x in complaints.my_complaints(c, s) if x["id"] == cid), "Reported")}
def t_complaint_confirm(c, s, b, m): complaints.tenant_confirm(c, s, int(m.group(1)), bool(b.get("fixed"))); return {"ok": True}
def t_leave_new(c, s, b, m): return {"id": leave_guests.create_leave(c, s, b.get("leave_date"), b.get("return_date") or None, (b.get("reason") or None), idem_key=b.get("idem_key"))}
def t_leave_cancel(c, s, b, m): leave_guests.cancel_leave(c, s, int(m.group(1))); return {"ok": True}
def t_guest_new(c, s, b, m): return leave_guests.create_guest_request(c, s, b.get("guest_name"), b.get("arrival"), b.get("departure"), bool(b.get("overnight")), bool(b.get("eats_food")), b.get("relationship") or None, idem_key=b.get("idem_key"))
def t_guest_cancel(c, s, b, m): leave_guests.cancel_guest(c, s, int(m.group(1))); return {"ok": True}
def t_assistant(c, s, b, m): return assistant.ask(c, s, b.get("text", ""))
def o_notice_new(c, s, b, m): return {"id": notices.create_notice(c, s, b.get("title"), b.get("body"), b.get("pinned"))}

ROUTES = [  # (method, regex, who, handler)   who: None = open, "any" = logged in, "tenant", "owner"
    ("POST", r"/api/login/tenant", None, login_tenant), ("POST", r"/api/login/owner", None, login_owner), ("POST", r"/api/join", None, join), ("GET", r"/api/invite/([\w-]+)", None, invite_info),
    ("GET", r"/api/me", "any", me), ("POST", r"/api/logout", "any", logout), ("GET", r"/api/notices", "any", lambda c, s, b, m: {"notices": notices.list_notices(c, s)}),
    ("GET", r"/api/tenant/home", "tenant", t_home), ("GET", r"/api/tenant/rent", "tenant", t_rent),
    ("GET", r"/api/tenant/complaints", "tenant", lambda c, s, b, m: {"complaints": complaints.my_complaints(c, s)}), ("POST", r"/api/tenant/complaints", "tenant", t_complaint_new),
    ("POST", r"/api/tenant/complaints/(\d+)/confirm", "tenant", t_complaint_confirm),
    ("GET", r"/api/tenant/leaves", "tenant", lambda c, s, b, m: {"leaves": leave_guests.my_leaves(c, s)}), ("POST", r"/api/tenant/leaves", "tenant", t_leave_new), ("POST", r"/api/tenant/leaves/(\d+)/cancel", "tenant", t_leave_cancel),
    ("GET", r"/api/tenant/guests", "tenant", lambda c, s, b, m: {"rules": leave_guests.guest_rules(c, s), "guests": leave_guests.my_guests(c, s)}), ("POST", r"/api/tenant/guests", "tenant", t_guest_new),
    ("POST", r"/api/tenant/guests/(\d+)/cancel", "tenant", t_guest_cancel), ("POST", r"/api/tenant/assistant", "tenant", t_assistant),
    ("GET", r"/api/tenant/notifications", "tenant", lambda c, s, b, m: {"notifications": notifications.my_notifications(c, s)}),
    ("POST", r"/api/tenant/notifications/read", "tenant", lambda c, s, b, m: (notifications.mark_all_read(c, s), {"ok": True})[1]),
    ("GET", r"/api/owner/dashboard", "owner", lambda c, s, b, m: dashboard.today_dashboard(c, s)), ("POST", r"/api/owner/notices", "owner", o_notice_new),
]
ROUTES = [(mth, re.compile(rx + "$"), who, fn) for mth, rx, who, fn in ROUTES]
def _cookie(tok, ttl): return [("Set-Cookie", f"sid={tok}; Path=/; Max-Age={ttl}; HttpOnly; SameSite=Strict" + ("; Secure" if os.environ.get("PGAPP_HTTPS") else ""))]

def map_error(e):
    msg = str(e) or "Something went wrong"
    if isinstance(e, AuthError): return (404 if msg == "Not found" else 403 if msg.endswith("only") else 401), msg
    if isinstance(e, (TenantError, setup.SetupError, ValueError, KeyError, TypeError)): return 400, msg if not isinstance(e, (KeyError, TypeError)) else "Please check the details and try again"
    return None

class Handler(BaseHTTPRequestHandler):
    server_version = "PGApp"; sys_version = ""
    def log_message(self, *a): pass
    def _send(self, status, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", CSP); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" or ctype.startswith("text/html") else "no-cache")
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)
    def do_GET(self): self._go("GET")
    def do_POST(self): self._go("POST")
    def _go(self, method):
        path = self.path.split("?")[0]
        if method == "GET" and not path.startswith("/api/"):
            name = "tenant.html" if path == "/" or path.startswith("/join/") else path[len("/static/"):] if path.startswith("/static/") else None
            if name in FILES: return self._send(200, open(os.path.join(STATIC, name), "rb").read(), FILES[name])
            return self._send(404, {"error": "Not found"})
        route = next(((r, rx.match(path)) for r in ROUTES for rx in [r[1]] if r[0] == method and rx.match(path)), None)
        if not route: return self._send(404, {"error": "Not found"})
        (_, _, who, fn), m = route; body = {}
        if method == "POST":
            if self.headers.get("X-PG") != "1": return self._send(403, {"error": "Blocked"})                    # CSRF guard (plus SameSite=Strict cookie)
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY: return self._send(413, {"error": "Too much data"})
            try: body = json.loads(self.rfile.read(n) or b"{}"); assert isinstance(body, dict)
            except Exception: return self._send(400, {"error": "Bad request"})
        if who is None and method == "POST" and path.startswith("/api/login") and not self.server.allow_login(self.client_address[0]): return self._send(429, {"error": "Too many attempts. Please wait a few minutes."})
        c = open_db(self.server.db_path)
        try:
            s = None
            if who:
                tok = SimpleCookie(self.headers.get("Cookie", "")).get("sid"); s = get_session(c, tok.value if tok else None)
                if who != "any" and s.role != who: raise AuthError(f"{who.capitalize()} only")
            out = fn(c, s, body, m); status, data, extra = out if isinstance(out, tuple) else (200, out, ())
            if fn is logout: core.logout(c, SimpleCookie(self.headers.get("Cookie", "")).get("sid").value)
            self._send(status, data, extra=extra)
        except Exception as e:
            r = map_error(e)
            if r is None: log.exception("server error"); r = (500, "Something went wrong on our side. Please try again.")
            self._send(r[0], {"error": r[1]})
        finally: c.close()

class App(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, db_path, port=8000, host="127.0.0.1"):
        init_db(db_path); self.db_path = db_path; self.tries = {}; super().__init__((host, port), Handler)
    def allow_login(self, ip, limit=30, window=600):
        now = time.time(); t = [x for x in self.tries.get(ip, []) if now - x < window]; t.append(now); self.tries[ip] = t; return len(t) <= limit

def scheduler_loop(db_path, backup_dir, every=300):
    while True:
        try:
            c = open_db(db_path)
            for (pg,) in c.execute("SELECT id FROM pgs").fetchall(): notifications.run_scheduler(c, pg)
            notifications.daily_backup_if_due(c, backup_dir); c.close()
        except Exception: log.exception("scheduler error")
        time.sleep(every)

if __name__ == "__main__":
    db = os.environ.get("PGAPP_DB", "pgapp.db"); cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "create-owner":
        init_db(db); c = open_db(db); core.create_pg_with_owner(c, input("PG name: "), input("Your name: "), input("Email: "), getpass.getpass("Password (8+ characters): ")); print("Done. Now run: python3 server.py run")
    else:
        logging.basicConfig(level=logging.INFO); app = App(db, int(os.environ.get("PORT", 8000)), os.environ.get("HOST", "127.0.0.1"))
        threading.Thread(target=scheduler_loop, args=(db, os.environ.get("PGAPP_BACKUPS", "backups")), daemon=True).start()
        print(f"Running on http://{app.server_address[0]}:{app.server_address[1]}"); app.serve_forever()
