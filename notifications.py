"""Module 8: notifications (delivery log, retries, 'Not delivered'), scheduler, backups, tenant data deletion.
Providers are plug-ins. In-app works today; SMS / WhatsApp / voice are honestly 'not connected' until a paid provider is registered."""
import os, glob, time, sqlite3, json
from datetime import datetime, date
import complaints, leave_guests, payments, dashboard
from dashboard import provider, register_schema, inr, IST
from complaints import _me
from core import audit, require_owner, AuthError, Session
from tenants import TenantError

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, rtype TEXT NOT NULL, rid INTEGER, to_label TEXT, kind TEXT NOT NULL, message TEXT NOT NULL,
  dedupe_key TEXT, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN('queued','sent','delivered','not_delivered')), attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL,
  last_error TEXT, created REAL, read_at REAL, UNIQUE(pg_id,dedupe_key));
CREATE TABLE IF NOT EXISTS notification_attempts(id INTEGER PRIMARY KEY, notification_id INTEGER NOT NULL, ts REAL, channel TEXT, ok INTEGER, detail TEXT);
CREATE TRIGGER IF NOT EXISTS att_no_change BEFORE UPDATE ON notification_attempts BEGIN SELECT RAISE(ABORT,'attempt log is append-only'); END;
CREATE TABLE IF NOT EXISTS deletion_requests(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'requested', requested_by INTEGER, requested_at REAL, completed_at REAL);
"""
register_schema(SCHEMA)
def _nc(name): return lambda to, msg: (False, f"{name} not connected", True)        # (ok, detail, permanent_failure)
PROVIDERS = {"in_app": lambda to, msg: (True, "stored in app inbox", False), "sms": _nc("SMS"), "whatsapp": _nc("WhatsApp"), "voice": _nc("Voice integration")}
CHANNELS = {"tenant": ["in_app"], "owner": ["in_app"], "staff": ["sms"], "backup": ["sms"]}   # order = fallback order
MAX_ATTEMPTS, BACKOFF = 3, [300, 1800]                                                           # retry after 5 min, then 30 min
def register_provider(name, fn): PROVIDERS[name] = fn
SYSTEM = lambda pg: Session(0, pg, "owner")        # for the scheduler only - never built from a web request
LABEL = {"queued": "Sending", "sent": "Sent", "delivered": "Delivered", "not_delivered": "Not delivered"}

def _recipient(c, pg, rtype, rid):
    if rtype == "tenant": r = c.execute("SELECT name,phone FROM tenants WHERE id=? AND pg_id=?", (rid, pg)).fetchone(); return (r["phone"], r["name"], "en") if r else (None, "?", "en")
    if rtype == "staff": r = c.execute("SELECT name,role,phone,language FROM staff_contacts WHERE id=? AND pg_id=?", (rid, pg)).fetchone(); return (r["phone"], f"{r['name']} ({r['role']})", r["language"]) if r else (None, "?", "en")
    if rtype == "backup": r = c.execute("SELECT backup_name,backup_phone FROM pg_settings WHERE pg_id=?", (pg,)).fetchone(); return (r and r["backup_phone"], f"{(r and r['backup_name']) or 'Backup contact'} (backup)", "en")
    return (None, "Owner", "en")

def _attempt(c, nid, now):
    n = c.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone(); phone = _recipient(c, n["pg_id"], n["rtype"], n["rid"])[0]; done, perm_all, detail = False, True, ""
    for ch in CHANNELS[n["rtype"]]:
        if ch != "in_app" and not phone: ok, detail, perm = False, "No phone number on file", True
        else:
            try: ok, detail, perm = PROVIDERS[ch](phone, n["message"])
            except Exception as e: ok, detail, perm = False, f"{type(e).__name__}: {e}", False
        c.execute("INSERT INTO notification_attempts(notification_id,ts,channel,ok,detail) VALUES(?,?,?,?,?)", (nid, now, ch, int(ok), detail))
        if ok: c.execute("UPDATE notifications SET status=?, attempts=attempts+1, last_error=NULL WHERE id=?", ("delivered" if ch == "in_app" else "sent", nid)); done = True; break
        perm_all = perm_all and perm
    if not done:
        att = n["attempts"] + 1; dead = perm_all or att >= MAX_ATTEMPTS
        c.execute("UPDATE notifications SET status=?, attempts=?, next_attempt=?, last_error=? WHERE id=?", ("not_delivered" if dead else "queued", att, None if dead else now + BACKOFF[att - 1], detail, nid))
    c.commit()

def notify(c, pg, rtype, rid, message, kind, key, now=None):
    """Saves the message FIRST, then tries to send. Same key twice = sent once. Returns None if it was a duplicate."""
    now = now or time.time(); label = _recipient(c, pg, rtype, rid)[1]
    cur = c.execute("INSERT OR IGNORE INTO notifications(pg_id,rtype,rid,to_label,kind,message,dedupe_key,next_attempt,created) VALUES(?,?,?,?,?,?,?,?,?)", (pg, rtype, rid, label, kind, message, key, now, now))
    c.commit()
    if not cur.rowcount: return None
    _attempt(c, cur.lastrowid, now); return cur.lastrowid

def retry_due(c, pg, now=None):
    now = now or time.time(); ids = [r[0] for r in c.execute("SELECT id FROM notifications WHERE pg_id=? AND status='queued' AND attempts>0 AND next_attempt<=?", (pg, now))]
    for i in ids: _attempt(c, i, now)
    return len(ids)

def _staff_text(lang, pg_name, room, what):
    if lang == "hi": return f"Namaste. {pg_name} se message hai. Room {room} mein complaint hai: {what}. Kripya check karein aur owner ko batayein."
    return f"Hello. This is {pg_name}. Room {room} has a complaint: {what}. Please check it and update the owner."   # no tenant name or phone is ever sent to staff

def run_scheduler(c, pg, now=None):
    """Run every few minutes (by the server, not by a person). Turns due reminders into notifications. Safe to run repeatedly."""
    s = SYSTEM(pg); now = now or time.time(); today = datetime.fromtimestamp(now, IST).date(); sent = 0; pgname = c.execute("SELECT name FROM pgs WHERE id=?", (pg,)).fetchone()[0]
    for e in complaints.check_deadlines(c, s, now):
        x = c.execute("SELECT * FROM complaints WHERE id=?", (e["complaint_id"],)).fetchone(); what = x["summary"] or x["description"][:80]; key = f"cmp:{x['id']}:{x['reopen_count']}:{e['level']}"
        if e["to"] == "staff":
            lang = _recipient(c, pg, "staff", x["assigned_staff_id"])[2]; r = notify(c, pg, "staff", x["assigned_staff_id"], _staff_text(lang, pgname, x["room_number"], what), "complaint_reminder", key, now)
        elif e["to"] == "owner": r = notify(c, pg, "owner", None, f"Complaint #{x['id']} (Room {x['room_number']}) is overdue: {what}", "complaint_overdue", key, now)
        else: r = notify(c, pg, "backup", None, f"{pgname}: complaint #{x['id']} (Room {x['room_number']}) is unresolved and the owner has not responded. Please check.", "complaint_backup", key, now)
        sent += bool(r)
    for t in c.execute("SELECT id FROM tenants WHERE pg_id=? AND status='active'", (pg,)).fetchall():
        for ch in payments._ledger(c, t["id"])[0]:
            if ch["remaining_paise"] <= 0: continue
            delta = (date.fromisoformat(ch["due_date"]) - today).days; amt = inr(ch["remaining_paise"])
            if 0 <= delta <= 3: r = notify(c, pg, "tenant", t["id"], f"Rent reminder: {amt} is due on {leave_guests._fmt(ch['due_date'])}.", "rent_reminder", f"rent-pre:{ch['id']}", now)
            elif delta < 0: r = notify(c, pg, "tenant", t["id"], f"Your rent is overdue: {amt} was due on {leave_guests._fmt(ch['due_date'])}. Please pay or speak to the PG owner.", "rent_overdue", f"rent-over:{ch['id']}:{(-delta - 1) // 7}", now)
            else: r = None
            sent += bool(r)
    for g in leave_guests.guest_reminders(c, s, today):
        if g["kind"] == "ends_today": sent += bool(notify(c, pg, "tenant", g["tenant_id"], f"Reminder: your guest {g['guest']} is due to leave today.", "guest_ending", f"gend-t:{g['guest_id']}", now))
        sent += bool(notify(c, pg, "owner", None, f"Guest {g['guest']} " + ("is due to leave today." if g["kind"] == "ends_today" else f"has stayed past {leave_guests._fmt(g['departure'])}."), "guest_" + g["kind"], f"g-{g['kind']}:{g['guest_id']}:{today}", now))
    for x in c.execute("SELECT * FROM complaints WHERE pg_id=? AND status='resolved' AND merged_into IS NULL", (pg,)).fetchall():
        who = {x["tenant_id"]} | {r[0] for r in c.execute("SELECT tenant_id FROM complaints WHERE merged_into=?", (x["id"],))}
        for tid in who: sent += bool(notify(c, pg, "tenant", tid, f"Your request has been marked resolved. Is the problem fixed? (Complaint #{x['id']})", "confirm_fixed", f"confirm:{x['id']}:{x['reopen_count']}:{tid}", now))
    return {"new_notifications": sent, "retried": retry_due(c, pg, now)}

def delivery_report(c, s, only_failed=False):
    require_owner(s)
    rows = c.execute("SELECT * FROM notifications WHERE pg_id=?" + (" AND status='not_delivered'" if only_failed else "") + " ORDER BY id DESC LIMIT 100", (s.pg_id,)).fetchall()
    return [{"id": r["id"], "to": r["to_label"], "kind": r["kind"], "status": LABEL[r["status"]], "reason": r["last_error"], "attempts": [dict(a) for a in c.execute("SELECT ts,channel,ok,detail FROM notification_attempts WHERE notification_id=?", (r["id"],))]} for r in rows]

def my_notifications(c, s):
    tid = _me(c, s); return [dict(r) for r in c.execute("SELECT id,message,kind,created,read_at FROM notifications WHERE pg_id=? AND rtype='tenant' AND rid=? ORDER BY id DESC LIMIT 50", (s.pg_id, tid))]
def owner_inbox(c, s):
    require_owner(s); return [dict(r) for r in c.execute("SELECT id,message,kind,created,read_at FROM notifications WHERE pg_id=? AND rtype='owner' ORDER BY id DESC LIMIT 50", (s.pg_id,))]

@provider
def _failed_deliveries(c, s, now):
    rows = c.execute("SELECT * FROM notifications WHERE pg_id=? AND status='not_delivered' AND created>=? ORDER BY id DESC", (s.pg_id, now - 172800)).fetchall(); out = []
    if rows: out.append({"severity": "orange", "label": "NOT DELIVERED", "kind": "not_delivered", "age_hours": 0, "text": f"{len(rows)} message{'s' * (len(rows) != 1)} could not be delivered - e.g. to {rows[0]['to_label']}: {rows[0]['last_error']}",
                         "detail": [{"to": r["to_label"], "kind": r["kind"], "reason": r["last_error"]} for r in rows]})
    for d in c.execute("SELECT d.*, t.name, t.status tstatus FROM deletion_requests d JOIN tenants t ON t.id=d.tenant_id WHERE d.pg_id=? AND d.status='requested'", (s.pg_id,)):
        out.append({"severity": "yellow", "label": "DATA REQUEST", "kind": "deletion", "age_hours": 0, "text": f"{d['name']} has asked for their personal data to be deleted" + ("" if d["tstatus"] == "moved_out" else " (after move-out)")})
    return out

# ---------------- backups ----------------
def verify_backup(path):
    try: db = sqlite3.connect(path); return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    except sqlite3.Error: return False
def backup_database(c, dest_dir, now=None, keep=14):
    """Checked copy of the whole database; keeps the newest 14. NOTE: still on the same server - an off-server copy (cloud storage) is a separate paid dependency."""
    c.commit(); os.makedirs(dest_dir, exist_ok=True); stamp = datetime.fromtimestamp(now or time.time(), IST).strftime("%Y%m%d")
    path = os.path.join(dest_dir, f"pgapp-{stamp}.db"); tmp = path + ".tmp"; dst = sqlite3.connect(tmp); c.backup(dst); dst.close()
    if not verify_backup(tmp): os.remove(tmp); raise RuntimeError("Backup failed verification")
    os.replace(tmp, path); os.chmod(path, 0o600)
    for old in sorted(glob.glob(os.path.join(dest_dir, "pgapp-*.db")))[:-keep]: os.remove(old)
    return path
def daily_backup_if_due(c, dest_dir, now=None):
    stamp = datetime.fromtimestamp(now or time.time(), IST).strftime("%Y%m%d")
    return None if os.path.exists(os.path.join(dest_dir, f"pgapp-{stamp}.db")) else backup_database(c, dest_dir, now)

# ---------------- tenant data deletion ----------------
def request_deletion(c, s, now=None):
    tid = _me(c, s); return _open_request(c, s.pg_id, tid, s.user_id, now)
def record_deletion_request(c, s, tenant_id, now=None):
    """For a former resident who asks the owner directly (their login is closed after move-out)."""
    require_owner(s); payments._tenant(c, s, tenant_id); return _open_request(c, s.pg_id, tenant_id, s.user_id, now)
def _open_request(c, pg, tid, by, now):
    old = c.execute("SELECT id FROM deletion_requests WHERE tenant_id=? AND status='requested'", (tid,)).fetchone()
    if old: return old[0]
    rid = c.execute("INSERT INTO deletion_requests(pg_id,tenant_id,requested_by,requested_at) VALUES(?,?,?,?)", (pg, tid, by, now or time.time())).lastrowid
    audit(c, pg, by, "deletion.requested", "tenant", tid); c.commit(); return rid

def process_deletion(c, s, request_id, confirm=False, now=None):
    """Owner-confirmed. Personal details are erased/anonymised; money records stay (accounting) with no name attached. Needs: moved out + dues settled."""
    require_owner(s); r = c.execute("SELECT * FROM deletion_requests WHERE id=? AND pg_id=? AND status='requested'", (request_id, s.pg_id)).fetchone()
    if not r: raise AuthError("Not found")
    t = c.execute("SELECT * FROM tenants WHERE id=?", (r["tenant_id"],)).fetchone()
    if t["status"] != "moved_out": raise TenantError("Complete the resident's move-out first")
    if payments.tenant_ledger(c, s, t["id"])["pending_paise"] > 0: raise TenantError("The resident still has unpaid dues - settle or void them first")
    if not confirm: raise TenantError(f"Owner confirmation required: this permanently erases {t['name']}'s personal details. Payment amounts stay, without their name.")
    tid, ph, nm, pg = t["id"], t["phone"], t["name"], s.pg_id
    for sql, a in (("UPDATE tenants SET name=?, phone=?, email=NULL, food_plan=NULL WHERE id=?", (f"Deleted resident {tid}", f"deleted-{tid}", tid)),
                   ("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE pg_id=? AND phone=? AND role='tenant')", (pg, ph)), ("DELETE FROM users WHERE pg_id=? AND phone=? AND role='tenant'", (pg, ph)),
                   ("DELETE FROM invites WHERE pg_id=? AND phone=?", (pg, ph)), ("UPDATE complaint_updates SET text='[removed]' WHERE complaint_id IN (SELECT id FROM complaints WHERE tenant_id=?)", (tid,)),
                   ("UPDATE complaints SET description='[removed at resident request]', summary=NULL WHERE tenant_id=?", (tid,)),
                   ("UPDATE guests SET guest_name='[removed]', relationship=NULL, arrival_time=NULL, decision_note=NULL WHERE tenant_id=?", (tid,)), ("UPDATE leaves SET reason=NULL WHERE tenant_id=?", (tid,)),
                   ("UPDATE notifications SET message='[removed]' WHERE pg_id=? AND rtype='tenant' AND rid=?", (pg, tid)), ("UPDATE charges SET note='[removed]' WHERE tenant_id=? AND note LIKE 'Guest:%'", (tid,))):
        c.execute(sql, a)
    c.execute("UPDATE audit_gate SET open=1")      # the ONLY place the audit log may be edited: names/phones inside old entries become '[deleted]'; rows themselves stay
    try:
        for row in c.execute("SELECT id, detail FROM audit_log WHERE pg_id=? AND detail IS NOT NULL AND ((entity='tenant' AND entity_id=?) OR detail LIKE ?)", (pg, tid, f"%{ph}%")).fetchall():
            d = row["detail"].replace(ph, "[deleted]").replace(nm, "[deleted]"); c.execute("UPDATE audit_log SET detail=? WHERE id=?", (d, row["id"]))
    finally: c.execute("UPDATE audit_gate SET open=0")
    c.execute("UPDATE deletion_requests SET status='completed', completed_at=? WHERE id=?", (now or time.time(), request_id))
    audit(c, pg, s.user_id, "tenant.data_deleted", "tenant", tid); c.commit()
