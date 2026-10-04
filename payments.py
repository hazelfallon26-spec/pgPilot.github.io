"""Module 4: rent charges, manual payments (partial/advance), reversals, security deposit, optional late fee.
Design: money = whole paise. Payments are APPEND-ONLY (a mistake is cancelled by a reversal row, never edited).
Pending amounts are always CALCULATED from charges and payments (oldest charge paid first), so they cannot drift."""
import re, time
from datetime import date, timedelta
import tenants
from core import audit, require_owner, AuthError
from setup import paise
from tenants import TenantError, _tenant

SCHEMA = """
CREATE TABLE IF NOT EXISTS charges(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  kind TEXT NOT NULL CHECK(kind IN('rent','late_fee','other')), period TEXT, amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
  due_date TEXT NOT NULL, note TEXT, voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT, created_by INTEGER, created REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_charge_per_period ON charges(tenant_id,period,kind) WHERE kind IN('rent','late_fee');
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  amount_paise INTEGER NOT NULL CHECK(amount_paise<>0), purpose TEXT NOT NULL CHECK(purpose IN('rent','deposit')),
  method TEXT NOT NULL, received_date TEXT NOT NULL, reference TEXT,
  entry_type TEXT NOT NULL CHECK(entry_type IN('payment','reversal')), reverses_id INTEGER UNIQUE REFERENCES payments(id), reason TEXT,
  idem_key TEXT, created_by INTEGER, created REAL, UNIQUE(pg_id,idem_key));
CREATE TRIGGER IF NOT EXISTS pay_no_update BEFORE UPDATE ON payments BEGIN SELECT RAISE(ABORT,'payments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS pay_no_delete BEFORE DELETE ON payments BEGIN SELECT RAISE(ABORT,'payments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS charge_no_delete BEFORE DELETE ON charges BEGIN SELECT RAISE(ABORT,'charges cannot be deleted, only voided'); END;
CREATE TABLE IF NOT EXISTS late_fee_config(pg_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL, amount_paise INTEGER NOT NULL, grace_days INTEGER NOT NULL, set_by INTEGER, set_at REAL);
"""
METHODS = {"cash", "upi", "bank", "other"}

def connect(path=":memory:"):
    c = tenants.connect(path); c.executescript(SCHEMA); return c
def _today(t): return date.fromisoformat(t) if isinstance(t, str) else (t or date.today())

def _ledger(c, tid):
    """Charges with paid/remaining (oldest due first) + unallocated advance credit."""
    ch = [dict(r) for r in c.execute("SELECT * FROM charges WHERE tenant_id=? AND voided=0 ORDER BY due_date,id", (tid,))]
    pool = c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE tenant_id=? AND purpose='rent'", (tid,)).fetchone()[0]
    for x in ch:
        x["paid_paise"] = min(x["amount_paise"], max(pool, 0)); pool -= x["paid_paise"]; x["remaining_paise"] = x["amount_paise"] - x["paid_paise"]
    return ch, max(pool, 0)

def _deposit(c, tid):
    st = c.execute("SELECT deposit_paise FROM stays WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    exp = st[0] if st else 0
    paid = c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE tenant_id=? AND purpose='deposit'", (tid,)).fetchone()[0]
    return {"expected_paise": exp, "paid_paise": paid, "status": "paid" if exp and paid >= exp else "partial" if paid else "not paid"}

def generate_rent_charges(c, s, period, today=None):
    """Creates one rent charge per current resident for 'YYYY-MM'. Safe to run twice. Refuses if the rent due day is not set.
    Rule flagged to owner: the FULL listed rent is charged (no pro-rating) - owner can void and add a custom charge."""
    require_owner(s)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period): raise TenantError("Period must look like 2030-10")
    day = c.execute("SELECT rent_due_day FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone()
    if not day or not day[0]: raise TenantError("Set the rent due day in PG settings first")
    due = f"{period}-{day[0]:02d}"; made = 0
    for st in c.execute("SELECT * FROM stays WHERE pg_id=? AND end_date IS NULL AND start_date<=?", (s.pg_id, f"{period}-31")).fetchall():
        if c.execute("INSERT OR IGNORE INTO charges(pg_id,tenant_id,kind,period,amount_paise,due_date,created_by,created) VALUES(?,?,?,?,?,?,?,?)",
                     (s.pg_id, st["tenant_id"], "rent", period, st["rent_paise"], due, s.user_id, time.time())).rowcount: made += 1
    audit(c, s.pg_id, s.user_id, "charges.generated", "pg", s.pg_id, {"period": period, "created": made}); c.commit(); return made

def add_charge(c, s, tenant_id, amount, due_date, note):
    require_owner(s); _tenant(c, s, tenant_id)
    cid = c.execute("INSERT INTO charges(pg_id,tenant_id,kind,amount_paise,due_date,note,created_by,created) VALUES(?,?,?,?,?,?,?,?)",
                    (s.pg_id, tenant_id, "other", paise(amount), date.fromisoformat(due_date).isoformat(), note, s.user_id, time.time())).lastrowid
    audit(c, s.pg_id, s.user_id, "charge.added", "charge", cid, {"amount_paise": paise(amount), "note": note}); c.commit(); return cid

def void_charge(c, s, charge_id, reason):
    """Cancels a charge (e.g. waive a late fee). A reason is required; the row is kept."""
    require_owner(s)
    if not reason.strip(): raise TenantError("A reason is required")
    r = c.execute("SELECT * FROM charges WHERE id=? AND pg_id=? AND voided=0", (charge_id, s.pg_id)).fetchone()
    if not r: raise AuthError("Not found")
    c.execute("UPDATE charges SET voided=1, void_reason=? WHERE id=?", (reason, charge_id))
    audit(c, s.pg_id, s.user_id, "charge.voided", "charge", charge_id, {"amount_paise": r["amount_paise"], "reason": reason}); c.commit()

def record_payment(c, s, tenant_id, amount, method, received_date=None, purpose="rent", reference=None, idem_key=None, today=None):
    """idem_key: the app sends one per button-press, so a retry on bad internet never records the money twice."""
    require_owner(s); _tenant(c, s, tenant_id)
    if method not in METHODS: raise TenantError(f"Method must be one of {sorted(METHODS)}")
    if purpose not in ("rent", "deposit"): raise TenantError("Purpose must be rent or deposit")
    try: amt = paise(amount)
    except Exception: raise TenantError("Amount must be more than zero")
    t = _today(today); rd = _today(received_date) if received_date else t
    if amt <= 0: raise TenantError("Amount must be more than zero")
    if rd > t: raise TenantError("Payment date cannot be in the future")
    if idem_key:
        old = c.execute("SELECT id FROM payments WHERE pg_id=? AND idem_key=?", (s.pg_id, idem_key)).fetchone()
        if old: return old[0]
    if purpose == "deposit":
        d = _deposit(c, tenant_id)
        if d["paid_paise"] + amt > d["expected_paise"]: raise TenantError("That is more than the security deposit amount")
    pid = c.execute("INSERT INTO payments(pg_id,tenant_id,amount_paise,purpose,method,received_date,reference,entry_type,idem_key,created_by,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (s.pg_id, tenant_id, amt, purpose, method, rd.isoformat(), reference, "payment", idem_key, s.user_id, time.time())).lastrowid
    audit(c, s.pg_id, s.user_id, "payment.recorded", "payment", pid, {"tenant_id": tenant_id, "amount_paise": amt, "purpose": purpose, "method": method}); c.commit(); return pid

def reverse_payment(c, s, payment_id, reason):
    """The only way to fix a wrong entry: add an opposite entry. Original stays visible forever."""
    require_owner(s)
    if not reason.strip(): raise TenantError("A reason is required")
    p = c.execute("SELECT * FROM payments WHERE id=? AND pg_id=?", (payment_id, s.pg_id)).fetchone()
    if not p: raise AuthError("Not found")
    if p["entry_type"] != "payment": raise TenantError("A reversal cannot be reversed - record a new payment instead")
    if c.execute("SELECT 1 FROM payments WHERE reverses_id=?", (payment_id,)).fetchone(): raise TenantError("Already reversed")
    rid = c.execute("INSERT INTO payments(pg_id,tenant_id,amount_paise,purpose,method,received_date,entry_type,reverses_id,reason,created_by,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (s.pg_id, p["tenant_id"], -p["amount_paise"], p["purpose"], p["method"], date.today().isoformat(), "reversal", payment_id, reason, s.user_id, time.time())).lastrowid
    audit(c, s.pg_id, s.user_id, "payment.reversed", "payment", rid, {"original": payment_id, "amount_paise": p["amount_paise"], "reason": reason}); c.commit(); return rid

def configure_late_fee(c, s, amount, grace_days, confirm=False):
    """OFF unless the owner sets it AND confirms. Flat fee, charged once per overdue month."""
    require_owner(s); a = paise(amount)
    if not confirm: raise TenantError(f"Owner confirmation required: a late fee of {a/100:g} will be charged once to any tenant still unpaid {int(grace_days)} days after the due date")
    c.execute("INSERT OR REPLACE INTO late_fee_config VALUES(?,?,?,?,?,?)", (s.pg_id, 1, a, int(grace_days), s.user_id, time.time()))
    audit(c, s.pg_id, s.user_id, "latefee.enabled", "pg", s.pg_id, {"amount_paise": a, "grace_days": int(grace_days)}); c.commit()

def disable_late_fee(c, s):
    require_owner(s); c.execute("UPDATE late_fee_config SET enabled=0 WHERE pg_id=?", (s.pg_id,)); audit(c, s.pg_id, s.user_id, "latefee.disabled", "pg", s.pg_id); c.commit()

def apply_late_fees(c, s, today=None):
    """Does NOTHING unless the owner enabled late fees. A fee the owner waived (voided) is never re-added."""
    require_owner(s); t = _today(today)
    cfg = c.execute("SELECT * FROM late_fee_config WHERE pg_id=? AND enabled=1", (s.pg_id,)).fetchone()
    if not cfg: return []
    added = []
    for tid in [r[0] for r in c.execute("SELECT DISTINCT tenant_id FROM charges WHERE pg_id=?", (s.pg_id,))]:
        for x in _ledger(c, tid)[0]:
            if x["kind"] == "rent" and x["remaining_paise"] > 0 and t > date.fromisoformat(x["due_date"]) + timedelta(days=cfg["grace_days"]):
                if c.execute("INSERT OR IGNORE INTO charges(pg_id,tenant_id,kind,period,amount_paise,due_date,note,created_by,created) VALUES(?,?,?,?,?,?,?,?,?)",
                             (s.pg_id, tid, "late_fee", x["period"], cfg["amount_paise"], (date.fromisoformat(x["due_date"]) + timedelta(days=cfg["grace_days"])).isoformat(),
                              "Late fee", s.user_id, time.time())).rowcount: added.append((tid, x["period"]))
    if added: audit(c, s.pg_id, s.user_id, "latefee.applied", "pg", s.pg_id, {"count": len(added)})
    c.commit(); return added

def tenant_ledger(c, s, tenant_id):
    require_owner(s); _tenant(c, s, tenant_id); ch, credit = _ledger(c, tenant_id)
    pays = [dict(r) for r in c.execute("SELECT * FROM payments WHERE tenant_id=? ORDER BY id", (tenant_id,))]
    return {"charges": ch, "pending_paise": sum(x["remaining_paise"] for x in ch), "advance_credit_paise": credit, "payments": pays, "deposit": _deposit(c, tenant_id)}

def pending_summary(c, s, today=None):
    """Feeds the dashboard and (later) reminders: who owes what, and what is overdue."""
    require_owner(s); t = _today(today).isoformat(); rows = []
    for r in c.execute("SELECT id,name FROM tenants WHERE pg_id=? AND status!='invited'", (s.pg_id,)).fetchall():
        ch, _ = _ledger(c, r["id"]); pend = sum(x["remaining_paise"] for x in ch); over = sum(x["remaining_paise"] for x in ch if x["due_date"] < t)
        if pend: rows.append({"tenant_id": r["id"], "name": r["name"], "pending_paise": pend, "overdue_paise": over, "status": "overdue" if over else "upcoming"})
    return {"total_pending_paise": sum(x["pending_paise"] for x in rows), "total_overdue_paise": sum(x["overdue_paise"] for x in rows), "rows": rows}

def my_payments(c, s):
    """Tenant's own rent screen: this month's rent, paid, remaining, due date, history, deposit."""
    if s.role != "tenant": raise AuthError("Tenant only")
    ph = c.execute("SELECT phone FROM users WHERE id=? AND pg_id=?", (s.user_id, s.pg_id)).fetchone()
    t = c.execute("SELECT id FROM tenants WHERE pg_id=? AND phone=? AND status!='moved_out'", (s.pg_id, ph["phone"])).fetchone()
    if not t: raise AuthError("Not found")
    ch, credit = _ledger(c, t["id"]); cur = next((x for x in ch if x["remaining_paise"] > 0), ch[-1] if ch else None)
    hist = [{"date": r["received_date"], "amount_paise": r["amount_paise"], "type": r["entry_type"], "method": r["method"]}
            for r in c.execute("SELECT * FROM payments WHERE tenant_id=? AND purpose='rent' ORDER BY id DESC", (t["id"],))]
    return {"current": cur and {k: cur[k] for k in ("kind", "amount_paise", "paid_paise", "remaining_paise", "due_date")},
            "total_pending_paise": sum(x["remaining_paise"] for x in ch), "advance_credit_paise": credit, "history": hist, "deposit": _deposit(c, t["id"])}

def prepare_move_out_prefilled(c, s, tenant_id, move_out_date, deductions=()):
    """Pre-fills dues from the ledger. Owner still has to confirm the final refund figure."""
    dues = tenant_ledger(c, s, tenant_id)["pending_paise"] / 100
    return tenants.prepare_move_out(c, s, tenant_id, move_out_date, dues=dues, deductions=deductions)
