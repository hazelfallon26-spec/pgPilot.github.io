"""Module 7: leave + guests. Rules: leave NEVER changes rent or food (owner-configured rules can come later);
guest limit breaches ALWAYS need owner approval; guest charges are only added to the ledger when the OWNER types the amount."""
import time
from datetime import date, datetime, timedelta
import dashboard, payments
from dashboard import provider, IST
from complaints import _me
from core import audit, require_owner, AuthError
from tenants import TenantError

SCHEMA = """
CREATE TABLE IF NOT EXISTS guest_settings(pg_id INTEGER PRIMARY KEY, always_require_approval INTEGER NOT NULL DEFAULT 0, night_charge_paise INTEGER, meal_charge_paise INTEGER);
CREATE TABLE IF NOT EXISTS leaves(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, leave_date TEXT NOT NULL, return_date TEXT, reason TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','cancelled','returned_early')), created REAL, cancelled_at REAL, idem_key TEXT, UNIQUE(pg_id,idem_key));
CREATE TABLE IF NOT EXISTS guests(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, guest_name TEXT NOT NULL, relationship TEXT,
  arrival TEXT NOT NULL, arrival_time TEXT, departure TEXT NOT NULL, overnight INTEGER NOT NULL, eats_food INTEGER NOT NULL, nights INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN('pending','approved','rejected','cancelled')), approval_reason TEXT, auto_approved INTEGER DEFAULT 0,
  suggested_charge_paise INTEGER, charge_id INTEGER, decision_note TEXT, decided_at REAL, departed_at REAL, created REAL, cancelled_at REAL, idem_key TEXT, UNIQUE(pg_id,idem_key));
"""
class RequestError(TenantError): pass
dashboard.register_schema(SCHEMA); connect = dashboard.connect
def _d(x): return x if isinstance(x, date) else date.fromisoformat(x)
def _today(t): return _d(t) if t else datetime.now(IST).date()
def _fmt(d): d = _d(d); return f"{d.day} {d.strftime('%b')}"
def _room(c, tid):
    r = c.execute("SELECT r.room_number FROM stays st JOIN beds b ON b.id=st.bed_id JOIN rooms r ON r.id=b.room_id WHERE st.tenant_id=? AND st.end_date IS NULL", (tid,)).fetchone()
    return r[0] if r else None

# ---------------- LEAVE ----------------
def create_leave(c, s, leave_date, return_date=None, reason=None, today=None, idem_key=None, now=None):
    tid = _me(c, s); td = _today(today); ld = _d(leave_date); rd = _d(return_date) if return_date else None
    if ld < td: raise RequestError("Leaving date cannot be in the past")
    if rd and rd < ld: raise RequestError("Return date is before leaving date")
    if idem_key:
        old = c.execute("SELECT id FROM leaves WHERE pg_id=? AND idem_key=?", (s.pg_id, idem_key)).fetchone()
        if old: return old[0]
    if c.execute("SELECT 1 FROM leaves WHERE tenant_id=? AND status='active' AND leave_date<=? AND (return_date IS NULL OR return_date>=?)", (tid, (rd or date.max).isoformat(), ld.isoformat())).fetchone():
        raise RequestError("You already have leave recorded for these dates")
    lid = c.execute("INSERT INTO leaves(pg_id,tenant_id,leave_date,return_date,reason,created,idem_key) VALUES(?,?,?,?,?,?,?)",
                    (s.pg_id, tid, ld.isoformat(), rd and rd.isoformat(), reason, now or time.time(), idem_key)).lastrowid
    audit(c, s.pg_id, s.user_id, "leave.created", "leave", lid, {"from": ld.isoformat(), "to": rd and rd.isoformat()}); c.commit(); return lid

def cancel_leave(c, s, leave_id, today=None, now=None):
    """Before it starts: cancelled. After it started: the tenant is back early - the record is kept with return date = today."""
    tid = _me(c, s); td = _today(today)
    x = c.execute("SELECT * FROM leaves WHERE id=? AND pg_id=? AND tenant_id=?", (leave_id, s.pg_id, tid)).fetchone()
    if not x: raise AuthError("Not found")
    if x["status"] != "active" or (x["return_date"] and x["return_date"] < td.isoformat()): raise RequestError("This leave is already over or cancelled")
    if x["leave_date"] <= td.isoformat():
        c.execute("UPDATE leaves SET status='returned_early', return_date=?, cancelled_at=? WHERE id=?", (td.isoformat(), now or time.time(), leave_id))
    else: c.execute("UPDATE leaves SET status='cancelled', cancelled_at=? WHERE id=?", (now or time.time(), leave_id))
    audit(c, s.pg_id, s.user_id, "leave.cancelled", "leave", leave_id); c.commit()

def list_leaves(c, s, today=None, include_past=False):
    require_owner(s); td = _today(today).isoformat()
    q = "SELECT l.*, t.name tenant FROM leaves l JOIN tenants t ON t.id=l.tenant_id WHERE l.pg_id=? AND l.status!='cancelled'" + ("" if include_past else " AND (l.return_date IS NULL OR l.return_date>=?)") + " ORDER BY l.leave_date"
    return [dict(r, room=_room(c, r["tenant_id"]), text=f"Away: {_fmt(r['leave_date'])} → {_fmt(r['return_date']) if r['return_date'] else 'return date not given'}",
                 away_today=r["leave_date"] <= td and (r["return_date"] is None or r["return_date"] >= td))
            for r in c.execute(q, (s.pg_id,) if include_past else (s.pg_id, td))]

def my_leaves(c, s):
    tid = _me(c, s); return [dict(r) for r in c.execute("SELECT id,leave_date,return_date,reason,status FROM leaves WHERE tenant_id=? ORDER BY id DESC", (tid,))]

# ---------------- GUESTS ----------------
def set_guest_policy(c, s, always_require_approval=None, night_charge=None, meal_charge=None):
    """Max nights lives in PG details (update_pg_details). Rates are optional and only ever SUGGEST a charge."""
    require_owner(s); c.execute("INSERT OR IGNORE INTO guest_settings(pg_id) VALUES(?)", (s.pg_id,))
    if always_require_approval is not None: c.execute("UPDATE guest_settings SET always_require_approval=? WHERE pg_id=?", (int(bool(always_require_approval)), s.pg_id))
    for col, v in (("night_charge_paise", night_charge), ("meal_charge_paise", meal_charge)):
        if v is not None: c.execute(f"UPDATE guest_settings SET {col}=? WHERE pg_id=?", (payments.paise(v), s.pg_id))
    audit(c, s.pg_id, s.user_id, "guest.policy_set", "pg", s.pg_id, {"always": always_require_approval, "night": night_charge, "meal": meal_charge}); c.commit()

def guest_rules(c, s):
    """Shown to the tenant BEFORE they submit."""
    _me(c, s); r = c.execute("SELECT guest_rules, guest_max_nights FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone()
    return {"rules_text": r and r["guest_rules"], "max_nights": r and r["guest_max_nights"]}

def _span_nights(c, tid, name, lo, hi):
    """Total nights of the same guest's connected requests - stops 'extension' requests from dodging the limit."""
    rows = [(date.fromisoformat(a), date.fromisoformat(d)) for a, d in c.execute("SELECT arrival,departure FROM guests WHERE tenant_id=? AND lower(guest_name)=? AND status IN('pending','approved') AND overnight=1", (tid, name.lower()))]
    moved = True
    while moved:
        moved = False
        for a, d in rows:
            if a <= hi and d >= lo and (a < lo or d > hi): lo, hi, moved = min(lo, a), max(hi, d), True
    return (hi - lo).days

def create_guest_request(c, s, guest_name, arrival, departure, overnight, eats_food=False, relationship=None, arrival_time=None, today=None, idem_key=None, now=None):
    tid = _me(c, s); td = _today(today); a, d = _d(arrival), _d(departure); name = " ".join((guest_name or "").split()); overnight = bool(overnight)
    if not 2 <= len(name) <= 80: raise RequestError("Please enter the guest's name")
    if a < td: raise RequestError("Arrival date cannot be in the past")
    if d < a or (overnight and d == a): raise RequestError("Departure must be after arrival for an overnight stay" if overnight else "Departure is before arrival")
    if idem_key:
        old = c.execute("SELECT id,status FROM guests WHERE pg_id=? AND idem_key=?", (s.pg_id, idem_key)).fetchone()
        if old: return {"id": old[0], "status": old[1]}
    mx = c.execute("SELECT guest_max_nights FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone(); mx = mx and mx[0]
    gs = c.execute("SELECT * FROM guest_settings WHERE pg_id=?", (s.pg_id,)).fetchone(); nights = _span_nights(c, tid, name, a, d) if overnight else 0
    new_nights = (d - a).days if overnight else 0; reason = None
    if overnight and mx is None: reason = "limit_not_set"
    elif overnight and nights > mx: reason = "over_limit"
    elif gs and gs["always_require_approval"]: reason = "policy"
    sug = None
    if gs and (gs["night_charge_paise"] is not None or gs["meal_charge_paise"] is not None):
        sug = new_nights * (gs["night_charge_paise"] or 0) + (max(new_nights, 1) * (gs["meal_charge_paise"] or 0) if eats_food else 0)
    status = "pending" if reason else "approved"
    gid = c.execute("INSERT INTO guests(pg_id,tenant_id,guest_name,relationship,arrival,arrival_time,departure,overnight,eats_food,nights,status,approval_reason,auto_approved,suggested_charge_paise,created,idem_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s.pg_id, tid, name, relationship, a.isoformat(), arrival_time, d.isoformat(), int(overnight), int(bool(eats_food)), new_nights, status, reason, int(not reason), sug, now or time.time(), idem_key)).lastrowid
    audit(c, s.pg_id, s.user_id, "guest.requested", "guest", gid, {"status": status, "reason": reason, "total_nights": nights}); c.commit()
    msg = "Approved" if not reason else {"over_limit": f"Waiting for owner approval - guests may stay {mx} night(s); this stay is {nights}", "limit_not_set": "Waiting for owner approval", "policy": "Waiting for owner approval"}[reason]
    return {"id": gid, "status": status, "reason": reason, "message": msg, "estimated_charge_paise": sug, "charge_note": None if sug is not None else "Any charges will be confirmed by the owner"}

def decide_guest(c, s, gid, approve, note=None, charge=None, now=None):
    """Owner decides. Over-limit approvals are allowed but logged as such. A charge reaches the rent ledger ONLY if the owner passes an amount here."""
    require_owner(s); g = c.execute("SELECT * FROM guests WHERE id=? AND pg_id=?", (gid, s.pg_id)).fetchone()
    if not g: raise AuthError("Not found")
    if g["status"] != "pending": raise RequestError("Already decided or cancelled")
    if not approve and not (note or "").strip(): raise RequestError("Please give the resident a reason for rejecting")
    cid = payments.add_charge(c, s, g["tenant_id"], charge, g["departure"], f"Guest: {g['guest_name']}") if approve and charge else None
    c.execute("UPDATE guests SET status=?, decision_note=?, decided_at=?, charge_id=? WHERE id=?", ("approved" if approve else "rejected", note, now or time.time(), cid, gid))
    audit(c, s.pg_id, s.user_id, "guest.approved" if approve else "guest.rejected", "guest", gid, {"reason_needed": g["approval_reason"], "charge_id": cid}); c.commit()

def cancel_guest(c, s, gid, today=None, now=None):
    tid = _me(c, s); g = c.execute("SELECT * FROM guests WHERE id=? AND pg_id=? AND tenant_id=?", (gid, s.pg_id, tid)).fetchone()
    if not g: raise AuthError("Not found")
    if g["status"] not in ("pending", "approved") or g["departure"] < _today(today).isoformat(): raise RequestError("This request can no longer be cancelled")
    c.execute("UPDATE guests SET status='cancelled', cancelled_at=? WHERE id=?", (now or time.time(), gid))
    audit(c, s.pg_id, s.user_id, "guest.cancelled", "guest", gid); c.commit()      # any charge is NOT removed automatically - owner is alerted on the dashboard

def mark_departed(c, s, gid, now=None):
    tid = None if s.role == "owner" else _me(c, s)
    g = c.execute("SELECT * FROM guests WHERE id=? AND pg_id=?" + ("" if tid is None else " AND tenant_id=?"), (gid, s.pg_id) + (() if tid is None else (tid,))).fetchone()
    if not g: raise AuthError("Not found")
    c.execute("UPDATE guests SET departed_at=? WHERE id=? AND departed_at IS NULL", (now or time.time(), gid)); audit(c, s.pg_id, s.user_id, "guest.departed", "guest", gid); c.commit()

_G = """SELECT g.*, t.name tenant FROM guests g JOIN tenants t ON t.id=g.tenant_id WHERE g.pg_id=? """
def guest_reminders(c, s, today=None):
    """For the notification module: stays ending today (remind tenant + owner) and overstays (owner)."""
    require_owner(s); td = _today(today).isoformat(); out = []
    for g in c.execute(_G + "AND g.status='approved' AND g.overnight=1 AND g.departed_at IS NULL AND g.departure<=?", (s.pg_id, td)):
        out.append({"guest_id": g["id"], "tenant_id": g["tenant_id"], "guest": g["guest_name"], "kind": "ends_today" if g["departure"] == td else "overstay", "departure": g["departure"]})
    return out
def my_guests(c, s):
    tid = _me(c, s); return [dict(r) for r in c.execute("SELECT id,guest_name,arrival,departure,overnight,eats_food,status,decision_note FROM guests WHERE tenant_id=? ORDER BY id DESC", (tid,))]

# ---------------- dashboard plug-in ----------------
@provider
def _attention(c, s, now):
    td = datetime.fromtimestamp(now, IST).date(); out = []; mx = (c.execute("SELECT guest_max_nights FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone() or [None])[0]
    for g in c.execute(_G + "AND g.status='pending'", (s.pg_id,)):
        why = {"over_limit": f" (limit {mx})", "limit_not_set": " (no guest limit set)", "policy": ""}[g["approval_reason"]]
        out.append({"severity": "orange", "label": "GUEST APPROVAL", "kind": "guest_approval", "guest_id": g["id"], "age_hours": (now - g["created"]) / 3600,
                    "text": f"Room {_room(c, g['tenant_id'])} — guest {g['guest_name']}, {g['nights']} night(s){why} — approval needed"})
    for r in guest_reminders(c, s, td):
        over = r["kind"] == "overstay"
        out.append({"severity": "orange" if over else "yellow", "label": "GUEST OVERSTAY" if over else "GUEST LEAVING", "kind": "guest_" + r["kind"], "age_hours": 0,
                    "text": f"Room {_room(c, r['tenant_id'])} — guest {r['guest']} " + (f"was due to leave on {_fmt(r['departure'])}" if over else "is due to leave today")})
    for g in c.execute(_G + "AND g.status='cancelled' AND g.charge_id IS NOT NULL AND g.charge_id IN (SELECT id FROM charges WHERE voided=0)", (s.pg_id,)):
        out.append({"severity": "yellow", "label": "CHECK", "kind": "guest_charge", "age_hours": 0, "text": f"Guest {g['guest_name']} was cancelled but a guest charge is still on {g['tenant']}'s account - void it if not needed"})
    for l in c.execute("SELECT l.*, t.name tenant FROM leaves l JOIN tenants t ON t.id=l.tenant_id WHERE l.pg_id=? AND (l.created>=? OR l.cancelled_at>=?)", (s.pg_id, now - 86400, now - 86400)):
        who = f"{l['tenant']} (Room {_room(c, l['tenant_id'])})"; span = f"{_fmt(l['leave_date'])} → {_fmt(l['return_date']) if l['return_date'] else 'return date not given'}"
        txt = f"{who} cancelled leave ({span})" if l["status"] == "cancelled" else f"{who} came back early" if l["status"] == "returned_early" else f"{who} will be away {span}"
        out.append({"severity": "yellow", "label": "LEAVE", "kind": "leave", "age_hours": 0, "text": txt})
    return out
