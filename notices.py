"""Notices: the owner posts, residents read. (Per-notice push messages come with the push-notification module.)"""
import time
from dashboard import register_schema
from core import audit, require_owner, AuthError
from tenants import TenantError
SCHEMA = "CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, created REAL);"
register_schema(SCHEMA)
def create_notice(c, s, title, body, pinned=False, now=None):
    require_owner(s); title, body = (title or "").strip(), (body or "").strip()
    if not 1 <= len(title) <= 120 or not 1 <= len(body) <= 3000: raise TenantError("A notice needs a title (up to 120 characters) and text (up to 3000)")
    nid = c.execute("INSERT INTO notices(pg_id,title,body,pinned,created) VALUES(?,?,?,?,?)", (s.pg_id, title, body, int(bool(pinned)), now or time.time())).lastrowid
    audit(c, s.pg_id, s.user_id, "notice.created", "notice", nid); c.commit(); return nid
def archive_notice(c, s, nid):
    require_owner(s)
    if not c.execute("UPDATE notices SET archived=1 WHERE id=? AND pg_id=?", (nid, s.pg_id)).rowcount: raise AuthError("Not found")
    audit(c, s.pg_id, s.user_id, "notice.archived", "notice", nid); c.commit()
def list_notices(c, s):
    return [dict(r) for r in c.execute("SELECT id,title,body,pinned,created FROM notices WHERE pg_id=? AND archived=0 ORDER BY pinned DESC, id DESC LIMIT 50", (s.pg_id,))]
