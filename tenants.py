"""Module 3: tenants, bed assignment, room moves (history kept), occupancy, move-out with owner-confirmed figures."""
import json, time
from datetime import date
import setup
from core import audit, require_owner, create_invite, normalize_phone, AuthError
from setup import paise

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL REFERENCES pgs(id),
  name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT, food_plan TEXT, move_in TEXT, expected_move_out TEXT,
  status TEXT NOT NULL DEFAULT 'invited' CHECK(status IN('invited','active','moved_out')), created REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_phone ON tenants(pg_id,phone) WHERE status!='moved_out';
CREATE TABLE IF NOT EXISTS stays(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  bed_id INTEGER NOT NULL REFERENCES beds(id), rent_paise INTEGER NOT NULL, deposit_paise INTEGER NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_occupant_per_bed ON stays(bed_id) WHERE end_date IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_bed_per_tenant ON stays(tenant_id) WHERE end_date IS NULL;
CREATE TABLE IF NOT EXISTS move_outs(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, stay_id INTEGER NOT NULL,
  move_out_date TEXT NOT NULL, deposit_paise INTEGER, dues_paise INTEGER, deductions TEXT, refund_paise INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','confirmed','cancelled')), confirmed_by INTEGER, confirmed_at REAL);
"""
class TenantError(Exception): pass

def connect(path=":memory:"):
    c = setup.connect(path); c.executescript(SCHEMA); return c

def _d(x):
    return date.fromisoformat(x).isoformat() if x else date.today().isoformat()
def _tenant(c, s, tid):
    t = c.execute("SELECT * FROM tenants WHERE id=? AND pg_id=?", (tid, s.pg_id)).fetchone()
    if not t: raise AuthError("Not found")
    return t
def _stay(c, tid):
    return c.execute("SELECT * FROM stays WHERE tenant_id=? AND end_date IS NULL", (tid,)).fetchone()
def _bed(c, s, bed_id):
    b = c.execute("SELECT b.*, r.status rstatus FROM beds b JOIN rooms r ON r.id=b.room_id WHERE b.id=? AND b.pg_id=?", (bed_id, s.pg_id)).fetchone()
    if not b: raise AuthError("Not found")
    return b

def add_tenant(c, s, name, phone, email=None, food_plan=None, expected_move_out=None):
    require_owner(s); phone = normalize_phone(phone)
    if c.execute("SELECT 1 FROM tenants WHERE pg_id=? AND phone=? AND status!='moved_out'", (s.pg_id, phone)).fetchone():
        raise TenantError("A current tenant already has this phone number")
    tid = c.execute("INSERT INTO tenants(pg_id,name,phone,email,food_plan,expected_move_out,created) VALUES(?,?,?,?,?,?,?)",
                    (s.pg_id, name, phone, email, food_plan, _d(expected_move_out) if expected_move_out else None, time.time())).lastrowid
    audit(c, s.pg_id, s.user_id, "tenant.added", "tenant", tid, {"name": name}); c.commit()
    return tid, create_invite(c, s, name, phone)

def assign_bed(c, s, tenant_id, bed_id, move_in=None, rent=None, deposit=None):
    """Rent/deposit default to the bed's listed amounts and are COPIED to the tenant, so later price
    changes on the bed never silently change an existing tenant's rent."""
    require_owner(s); t = _tenant(c, s, tenant_id); b = _bed(c, s, bed_id)
    if t["status"] == "moved_out": raise TenantError("This tenant has moved out")
    if _stay(c, tenant_id): raise TenantError("Tenant already has a bed - use move instead")
    if b["rstatus"] != "active": raise TenantError("This room is not available (maintenance/closed)")
    if c.execute("SELECT 1 FROM stays WHERE bed_id=? AND end_date IS NULL", (bed_id,)).fetchone(): raise TenantError("Bed is already occupied")
    r = paise(rent) if rent is not None else b["rent_paise"]; d = paise(deposit) if deposit is not None else b["deposit_paise"]
    start = _d(move_in)
    c.execute("INSERT INTO stays(pg_id,tenant_id,bed_id,rent_paise,deposit_paise,start_date) VALUES(?,?,?,?,?,?)", (s.pg_id, tenant_id, bed_id, r, d, start))
    c.execute("UPDATE tenants SET status='active', move_in=? WHERE id=?", (start, tenant_id))
    audit(c, s.pg_id, s.user_id, "tenant.assigned", "tenant", tenant_id, {"bed_id": bed_id, "rent_paise": r, "deposit_paise": d, "overridden": rent is not None or deposit is not None})
    c.commit()

def move_tenant(c, s, tenant_id, new_bed_id, move_date=None, new_rent=None):
    """History is kept (old stay is closed, new one opened). Rent and deposit CARRY OVER unchanged unless the
    owner explicitly passes new_rent. A warning is returned if the new bed's listed rent differs."""
    require_owner(s); _tenant(c, s, tenant_id); old = _stay(c, tenant_id); nb = _bed(c, s, new_bed_id); when = _d(move_date)
    if not old: raise TenantError("Tenant has no current bed")
    if old["bed_id"] == new_bed_id: raise TenantError("Tenant is already in this bed")
    if when < old["start_date"]: raise TenantError("Move date is before the current stay began")
    if nb["rstatus"] != "active": raise TenantError("That room is not available")
    if c.execute("SELECT 1 FROM stays WHERE bed_id=? AND end_date IS NULL", (new_bed_id,)).fetchone(): raise TenantError("That bed is occupied")
    rent = paise(new_rent) if new_rent is not None else old["rent_paise"]
    c.execute("UPDATE stays SET end_date=? WHERE id=?", (when, old["id"]))
    c.execute("INSERT INTO stays(pg_id,tenant_id,bed_id,rent_paise,deposit_paise,start_date) VALUES(?,?,?,?,?,?)", (s.pg_id, tenant_id, new_bed_id, rent, old["deposit_paise"], when))
    audit(c, s.pg_id, s.user_id, "tenant.moved", "tenant", tenant_id, {"from_bed": old["bed_id"], "to_bed": new_bed_id, "rent_paise": [old["rent_paise"], rent]})
    c.commit()
    warn = None if rent == nb["rent_paise"] else f"New bed's listed rent is {nb['rent_paise']/100:g}; tenant keeps {rent/100:g}. Pass new_rent to change it."
    return {"rent_changed": rent != old["rent_paise"], "warning": warn}

def change_tenant_rent(c, s, tenant_id, new_rent, confirm=False):
    require_owner(s); _tenant(c, s, tenant_id); st = _stay(c, tenant_id)
    if not st: raise TenantError("Tenant has no current bed")
    new = paise(new_rent)
    if not confirm: raise TenantError(f"Owner confirmation required: rent would change from {st['rent_paise']/100:g} to {new/100:g}")
    c.execute("UPDATE stays SET rent_paise=? WHERE id=?", (new, st["id"]))
    audit(c, s.pg_id, s.user_id, "tenant.rent_changed", "tenant", tenant_id, {"rent_paise": [st["rent_paise"], new]}); c.commit()

def room_overview(c, s):
    require_owner(s)
    rows = c.execute("""SELECT f.number fl, r.room_number rn, r.status, b.label, t.name who FROM rooms r
      JOIN floors f ON f.id=r.floor_id JOIN beds b ON b.room_id=r.id
      LEFT JOIN stays st ON st.bed_id=b.id AND st.end_date IS NULL LEFT JOIN tenants t ON t.id=st.tenant_id
      WHERE r.pg_id=? ORDER BY f.number, r.room_number, b.label""", (s.pg_id,)).fetchall()
    rooms = {}
    for r in rows:
        rm = rooms.setdefault((r["fl"], r["rn"]), {"floor": r["fl"], "room": r["rn"], "status": r["status"], "beds": []})
        rm["beds"].append({"bed": r["label"], "occupant": r["who"]})
    out = list(rooms.values())
    for rm in out: rm["occupied"] = sum(1 for b in rm["beds"] if b["occupant"]); rm["capacity"] = len(rm["beds"])
    usable = [rm for rm in out if rm["status"] == "active"]
    total = sum(rm["capacity"] for rm in usable); occ = sum(rm["occupied"] for rm in out)
    return {"rooms": out, "total_beds": total, "occupied": occ, "vacant": sum(rm["capacity"] - rm["occupied"] for rm in usable)}

def prepare_move_out(c, s, tenant_id, move_out_date, dues=0, deductions=()):
    """Creates a DRAFT with calculated figures. Nothing changes until confirm_move_out.
    dues / deductions are owner-entered for now (the Payments module will pre-fill dues, owner still confirms)."""
    require_owner(s); _tenant(c, s, tenant_id); st = _stay(c, tenant_id)
    if not st: raise TenantError("Tenant has no current bed")
    when = _d(move_out_date)
    if when < st["start_date"]: raise TenantError("Move-out date is before move-in")
    ded = [(l, paise(a)) for l, a in deductions]; due = paise(dues)
    refund = st["deposit_paise"] - due - sum(a for _, a in ded)   # negative => tenant still owes
    c.execute("UPDATE move_outs SET status='cancelled' WHERE tenant_id=? AND status='draft'", (tenant_id,))
    mid = c.execute("INSERT INTO move_outs(pg_id,tenant_id,stay_id,move_out_date,deposit_paise,dues_paise,deductions,refund_paise) VALUES(?,?,?,?,?,?,?,?)",
                    (s.pg_id, tenant_id, st["id"], when, st["deposit_paise"], due, json.dumps(ded), refund)).lastrowid
    audit(c, s.pg_id, s.user_id, "moveout.prepared", "tenant", tenant_id, {"refund_paise": refund}); c.commit()
    return {"move_out_id": mid, "deposit": st["deposit_paise"] / 100, "dues": due / 100, "deductions": [(l, a / 100) for l, a in ded],
            "refund": refund / 100, "tenant_owes": -refund / 100 if refund < 0 else 0}

def confirm_move_out(c, s, move_out_id, confirmed_refund):
    """Owner must type back the exact refund figure they reviewed. Frees the bed, disables the tenant's login.
    This records the amount only - it does not pay anything."""
    require_owner(s)
    m = c.execute("SELECT * FROM move_outs WHERE id=? AND pg_id=?", (move_out_id, s.pg_id)).fetchone()
    if not m: raise AuthError("Not found")
    if m["status"] != "draft": raise TenantError("This move-out is no longer a draft")
    if round(float(confirmed_refund) * 100) != m["refund_paise"]: raise TenantError("Confirmed figure does not match the calculation")
    st = c.execute("SELECT * FROM stays WHERE id=? AND end_date IS NULL", (m["stay_id"],)).fetchone()
    if not st: raise TenantError("Tenant's bed assignment changed - prepare the move-out again")
    t = _tenant(c, s, m["tenant_id"])
    c.execute("UPDATE stays SET end_date=? WHERE id=?", (m["move_out_date"], st["id"]))
    c.execute("UPDATE tenants SET status='moved_out' WHERE id=?", (t["id"],))
    c.execute("UPDATE move_outs SET status='confirmed', confirmed_by=?, confirmed_at=? WHERE id=?", (s.user_id, time.time(), move_out_id))
    uid = c.execute("SELECT id FROM users WHERE pg_id=? AND role='tenant' AND phone=?", (s.pg_id, t["phone"])).fetchone()
    if uid:
        c.execute("UPDATE users SET active=0 WHERE id=?", (uid[0],)); c.execute("DELETE FROM sessions WHERE user_id=?", (uid[0],))
    audit(c, s.pg_id, s.user_id, "moveout.confirmed", "tenant", t["id"], {"refund_paise": m["refund_paise"], "dues_paise": m["dues_paise"]}); c.commit()

def my_profile(c, s):
    """A tenant sees ONLY their own record (no roommates, no other tenants)."""
    if s.role != "tenant": raise AuthError("Tenant only")
    u = c.execute("SELECT phone FROM users WHERE id=? AND pg_id=?", (s.user_id, s.pg_id)).fetchone()
    r = c.execute("""SELECT t.name,t.phone,t.email,t.food_plan,t.move_in,t.expected_move_out,f.number floor,rm.room_number room,b.label bed,st.rent_paise,st.deposit_paise
      FROM tenants t LEFT JOIN stays st ON st.tenant_id=t.id AND st.end_date IS NULL LEFT JOIN beds b ON b.id=st.bed_id
      LEFT JOIN rooms rm ON rm.id=b.room_id LEFT JOIN floors f ON f.id=rm.floor_id
      WHERE t.pg_id=? AND t.phone=? AND t.status!='moved_out'""", (s.pg_id, u["phone"])).fetchone()
    if not r: raise AuthError("Not found")
    return dict(r)
