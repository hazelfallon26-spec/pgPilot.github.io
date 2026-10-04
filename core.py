"""PG management V1 - foundation: database, auth, sessions, pg_id isolation, audit log.
SQLite now; every table carries pg_id so Postgres + multi-PG can come later."""
import sqlite3, secrets, hashlib, hmac, time, json
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS pgs(id INTEGER PRIMARY KEY, name TEXT NOT NULL, created REAL);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  role TEXT NOT NULL CHECK(role IN('owner','tenant')), name TEXT NOT NULL,
  email TEXT, phone TEXT, salt BLOB NOT NULL, secret_hash TEXT NOT NULL,
  failed INTEGER DEFAULT 0, locked_until REAL DEFAULT 0, active INTEGER DEFAULT 1,
  UNIQUE(pg_id,email), UNIQUE(pg_id,phone));
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, pg_id INTEGER NOT NULL,
  role TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites(
  token_hash TEXT PRIMARY KEY, pg_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL,
  expires REAL NOT NULL, used_at REAL, created_by INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, pg_id INTEGER, actor_id INTEGER,
  action TEXT NOT NULL, entity TEXT, entity_id INTEGER, detail TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
"""
OWNER_SESSION_SECS = 7 * 86400     # decision: owner stays logged in 7 days
TENANT_SESSION_SECS = 30 * 86400   # decision: tenants 30 days (poor internet, no training)
MAX_FAILS, LOCK_SECS = 5, 15 * 60

class AuthError(Exception): pass

def normalize_phone(p):
    """Same number typed as '+91 98765-43210' or '09876543210' must match. Returns 10 digits."""
    d = "".join(ch for ch in str(p) if ch.isdigit())
    if len(d) == 12 and d.startswith("91"): d = d[2:]
    if len(d) == 11 and d.startswith("0"): d = d[1:]
    if len(d) != 10 or d[0] not in "6789": raise AuthError("Enter a valid 10-digit mobile number")
    return d

@dataclass
class Session:
    user_id: int; pg_id: int; role: str

def connect(path=":memory:"):
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.executescript(SCHEMA); return c

def audit(c, pg_id, actor_id, action, entity=None, entity_id=None, detail=None):
    c.execute("INSERT INTO audit_log(ts,pg_id,actor_id,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?,?)",
              (time.time(), pg_id, actor_id, action, entity, entity_id, json.dumps(detail) if detail else None))

def _hash(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=salt, n=2**14, r=8, p=1).hex()
def _th(token): return hashlib.sha256(token.encode()).hexdigest()  # only hashes are stored

def _new_user(c, pg_id, role, name, secret, email=None, phone=None):
    salt = secrets.token_bytes(16)
    cur = c.execute("INSERT INTO users(pg_id,role,name,email,phone,salt,secret_hash) VALUES(?,?,?,?,?,?,?)",
                    (pg_id, role, name, email and email.lower().strip(), phone, salt, _hash(secret, salt)))
    return cur.lastrowid

def _start_session(c, uid, pg_id, role):
    tok = secrets.token_urlsafe(32)
    ttl = OWNER_SESSION_SECS if role == "owner" else TENANT_SESSION_SECS
    c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (_th(tok), uid, pg_id, role, time.time() + ttl))
    return tok

def create_pg_with_owner(c, pg_name, owner_name, email, password):
    if len(password) < 8: raise AuthError("Password must be at least 8 characters")
    pg_id = c.execute("INSERT INTO pgs(name,created) VALUES(?,?)", (pg_name, time.time())).lastrowid
    uid = _new_user(c, pg_id, "owner", owner_name, password, email=email)
    audit(c, pg_id, uid, "pg.created", "pg", pg_id); c.commit(); return pg_id, uid

def _check(c, users, secret):
    now = time.time()
    for u in users:
        if u["locked_until"] > now or not u["active"]: continue
        if hmac.compare_digest(_hash(secret, u["salt"]), u["secret_hash"]):
            c.execute("UPDATE users SET failed=0 WHERE id=?", (u["id"],)); return u
        f = u["failed"] + 1
        c.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?",
                  (0 if f >= MAX_FAILS else f, now + LOCK_SECS if f >= MAX_FAILS else 0, u["id"]))
        audit(c, u["pg_id"], u["id"], "login.failed", "user", u["id"])
    c.commit(); raise AuthError("Wrong details, or account temporarily locked")  # same message always

def login_owner(c, email, password):
    us = c.execute("SELECT * FROM users WHERE role='owner' AND email=?", (email.lower().strip(),)).fetchall()
    u = _check(c, us, password)
    tok = _start_session(c, u["id"], u["pg_id"], "owner"); audit(c, u["pg_id"], u["id"], "login"); c.commit(); return tok

def login_tenant(c, phone, pin):
    phone = normalize_phone(phone)
    us = c.execute("SELECT * FROM users WHERE role='tenant' AND phone=?", (phone,)).fetchall()
    u = _check(c, us, pin)
    tok = _start_session(c, u["id"], u["pg_id"], "tenant"); audit(c, u["pg_id"], u["id"], "login"); c.commit(); return tok

def get_session(c, token):
    r = c.execute("SELECT * FROM sessions WHERE token_hash=?", (_th(token or ""),)).fetchone()
    if not r or r["expires"] < time.time(): raise AuthError("Please log in again")
    return Session(r["user_id"], r["pg_id"], r["role"])

def logout(c, token):
    c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),)); c.commit()

def require_owner(s):
    if s.role != "owner": raise AuthError("Owner only")

def create_invite(c, s, name, phone):
    require_owner(s); phone = normalize_phone(phone)
    tok = secrets.token_urlsafe(24)
    c.execute("INSERT INTO invites VALUES(?,?,?,?,?,NULL,?)",
              (_th(tok), s.pg_id, name, phone, time.time() + 7 * 86400, s.user_id))
    audit(c, s.pg_id, s.user_id, "invite.created", "tenant", None, {"name": name, "phone": phone})
    c.commit(); return tok   # owner shares this inside a link

def accept_invite(c, token, pin):
    if not (pin.isdigit() and 4 <= len(pin) <= 6) or len(set(pin)) == 1 or pin in "0123456789":
        raise AuthError("Choose a 4-6 digit PIN that is not simple (like 1234 or 0000)")
    inv = c.execute("SELECT * FROM invites WHERE token_hash=?", (_th(token),)).fetchone()
    if not inv or inv["used_at"] or inv["expires"] < time.time(): raise AuthError("Invite is invalid or expired")
    uid = _new_user(c, inv["pg_id"], "tenant", inv["name"], pin, phone=inv["phone"])
    c.execute("UPDATE invites SET used_at=? WHERE token_hash=?", (time.time(), _th(token)))
    audit(c, inv["pg_id"], uid, "invite.accepted", "user", uid)
    tok = _start_session(c, uid, inv["pg_id"], "tenant"); c.commit(); return tok

SCOPED_TABLES = {"users", "invites"}   # extend as modules are added
def scoped_get(c, s, table, row_id):
    """The ONLY way modules read a row by id: pg_id always comes from the session, never from the request.
    Tenants may only read their own user row."""
    if table not in SCOPED_TABLES: raise ValueError("unknown table")
    r = c.execute(f"SELECT * FROM {table} WHERE id=? AND pg_id=?", (row_id, s.pg_id)).fetchone() if table == "users" else None
    if r is None or (s.role == "tenant" and r["id"] != s.user_id): raise AuthError("Not found")
    return r
