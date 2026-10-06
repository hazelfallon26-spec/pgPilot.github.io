"""Owner API: thin wrappers over the tested modules. Every handler runs as the logged-in owner, so pg_id always comes from the session."""
import time
from datetime import datetime
import complaints, payments, tenants, setup, leave_guests, notifications, dashboard, assistant, notices, core
from dashboard import inr, IST
from core import require_owner
from tenants import TenantError
from leave_guests import _fmt

def M(p): return {"paise": p, "text": inr(p)}
def _today(): return datetime.now(IST).date().isoformat()
def _opt(v): return None if v in (None, "") else v

def residents(c, s, q, m):
    rows = c.execute("""SELECT t.id,t.name,t.phone,t.status,r.room_number,b.label bed,f.number floor FROM tenants t LEFT JOIN stays st ON st.tenant_id=t.id AND st.end_date IS NULL
        LEFT JOIN beds b ON b.id=st.bed_id LEFT JOIN rooms r ON r.id=b.room_id LEFT JOIN floors f ON f.id=r.floor_id WHERE t.pg_id=? AND t.status!='moved_out' ORDER BY f.number, r.room_number, t.name""", (s.pg_id,)).fetchall()
    return {"residents": [dict(r, pending=M(sum(x["remaining_paise"] for x in payments._ledger(c, r["id"])[0]))) for r in rows]}
def add_resident(c, s, b, m):
    tid, tok = tenants.add_tenant(c, s, b.get("name", "").strip(), b.get("phone", ""), _opt(b.get("email")), _opt(b.get("food_plan")))
    return {"id": tid, "invite_path": f"/join/{tok}"}
def resend_invite(c, s, b, m):
    t = tenants._tenant(c, s, int(m.group(1)))
    if c.execute("SELECT 1 FROM users WHERE pg_id=? AND role='tenant' AND phone=?", (s.pg_id, t["phone"])).fetchone() or t["status"] == "moved_out": raise TenantError("This resident has already joined")
    return {"invite_path": "/join/" + core.create_invite(c, s, t["name"], t["phone"])}
def resident(c, s, q, m):
    tid = int(m.group(1)); t = tenants._tenant(c, s, tid); L = payments.tenant_ledger(c, s, tid)
    st = c.execute("SELECT r.room_number,b.label,f.number floor,st.rent_paise,st.deposit_paise,st.start_date FROM stays st JOIN beds b ON b.id=st.bed_id JOIN rooms r ON r.id=b.room_id JOIN floors f ON f.id=r.floor_id WHERE st.tenant_id=? AND st.end_date IS NULL", (tid,)).fetchone()
    rev = {p["reverses_id"] for p in L["payments"] if p["reverses_id"]}
    return {"id": tid, "name": t["name"], "phone": t["phone"], "email": t["email"], "status": t["status"], "food_plan": t["food_plan"], "move_in": t["move_in"],
            "stay": st and {"room": st["room_number"], "bed": st["label"], "floor": st["floor"], "rent": M(st["rent_paise"]), "deposit": M(st["deposit_paise"])},
            "pending": M(L["pending_paise"]), "advance": M(L["advance_credit_paise"]), "deposit_status": {"expected": M(L["deposit"]["expected_paise"]), "paid": M(L["deposit"]["paid_paise"]), "status": L["deposit"]["status"]},
            "charges": [{"id": x["id"], "kind": x["kind"], "period": x["period"], "note": x["note"], "amount": M(x["amount_paise"]), "remaining": M(x["remaining_paise"]), "due": _fmt(x["due_date"])} for x in L["charges"]],
            "payments": [{"id": p["id"], "date": _fmt(p["received_date"]), "amount": M(p["amount_paise"]), "type": p["entry_type"], "method": p["method"], "purpose": p["purpose"], "reason": p["reason"], "can_reverse": p["entry_type"] == "payment" and p["id"] not in rev} for p in reversed(L["payments"])]}
def assign(c, s, b, m): tenants.assign_bed(c, s, int(m.group(1)), int(b["bed_id"]), _opt(b.get("move_in")), rent=_opt(b.get("rent"))); return {"ok": True}
def move(c, s, b, m): return tenants.move_tenant(c, s, int(m.group(1)), int(b["bed_id"]), _opt(b.get("move_date")), _opt(b.get("new_rent")))
def moveout_prepare(c, s, b, m):
    p = payments.prepare_move_out_prefilled(c, s, int(m.group(1)), b.get("date"), [(d["label"], d["amount"]) for d in b.get("deductions", []) if d.get("label") and d.get("amount")])
    f = lambda x: inr(round(x * 100)); return {"move_out_id": p["move_out_id"], "deposit": f(p["deposit"]), "dues": f(p["dues"]), "deductions": [{"label": l, "amount": f(a)} for l, a in p["deductions"]], "refund": f(p["refund"]), "refund_number": p["refund"], "tenant_owes": p["tenant_owes"] > 0}
def moveout_confirm(c, s, b, m): tenants.confirm_move_out(c, s, int(m.group(1)), b["refund"]); return {"ok": True}

def rooms(c, s, q, m):
    ov = tenants.room_overview(c, s); return ov
def meta(c, s, q, m):
    complaints.ensure_defaults(c, s.pg_id)
    return {"categories": [r[0] for r in c.execute("SELECT name FROM complaint_categories WHERE pg_id=? AND active=1 ORDER BY id", (s.pg_id,))],
            "staff": [dict(r) for r in c.execute("SELECT id,name,role FROM staff_contacts WHERE pg_id=? AND active=1", (s.pg_id,))],
            "vacant_beds": [{"bed_id": r["id"], "label": f"Floor {r['fl']} · Room {r['rn']} · Bed {r['label']}", "rent": M(r["rent_paise"])} for r in c.execute(
                """SELECT b.id,b.label,b.rent_paise,r.room_number rn,f.number fl FROM beds b JOIN rooms r ON r.id=b.room_id JOIN floors f ON f.id=r.floor_id WHERE b.pg_id=? AND r.status='active'
                   AND NOT EXISTS(SELECT 1 FROM stays st WHERE st.bed_id=b.id AND st.end_date IS NULL) ORDER BY f.number,r.room_number,b.label""", (s.pg_id,))]}

def payments_page(c, s, q, m):
    p = payments.pending_summary(c, s, _today())
    return {"total_pending": M(p["total_pending_paise"]), "total_overdue": M(p["total_overdue_paise"]), "rows": [{"tenant_id": r["tenant_id"], "name": r["name"], "pending": M(r["pending_paise"]), "overdue": M(r["overdue_paise"]), "status": r["status"]} for r in p["rows"]],
            "tenants": [dict(r) for r in c.execute("SELECT id,name FROM tenants WHERE pg_id=? AND status!='moved_out' ORDER BY name", (s.pg_id,))], "period": datetime.now(IST).strftime("%Y-%m")}
def record_payment(c, s, b, m): return {"id": payments.record_payment(c, s, int(b["tenant_id"]), b["amount"], b["method"], _opt(b.get("received_date")), b.get("purpose") or "rent", _opt(b.get("reference")), b.get("idem_key"))}
def reverse_payment(c, s, b, m): return {"id": payments.reverse_payment(c, s, int(m.group(1)), b.get("reason", ""))}
def void_charge(c, s, b, m): payments.void_charge(c, s, int(m.group(1)), b.get("reason", "")); return {"ok": True}
def gen_charges(c, s, b, m): return {"created": payments.generate_rent_charges(c, s, b.get("period", ""))}

def complaints_list(c, s, q, m):
    kw = {k: q[k] for k in ("category", "priority", "status", "room") if q.get(k)}
    if q.get("floor"): kw["floor"] = int(q["floor"])
    rows = complaints.list_complaints(c, s, time.time(), open_only=q.get("open") == "1", **kw)
    if q.get("overdue") == "1": rows = [r for r in rows if r["overdue"]]
    if q.get("recent_hours"): rows = [r for r in rows if r["hours_open"] <= float(q["recent_hours"])]
    st = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM staff_contacts WHERE pg_id=?", (s.pg_id,))}
    return {"complaints": [{"id": r["id"], "room": r["room_number"], "floor": r["floor_number"], "category": r["category"], "text": r["description"], "summary": r["summary"], "priority": r["priority"], "status": r["status"], "hours_open": r["hours_open"],
            "overdue": r["overdue"], "tenant": r["tenant"], "staff": st.get(r["assigned_staff_id"]), "staff_id": r["assigned_staff_id"], "reopened": r["reopen_count"], "duplicate_of": r["possible_duplicate_of"], "ai_state": r["ai_state"], "danger_word": bool(r["keyword_forced"])} for r in rows]}
def c_correct(c, s, b, m): complaints.correct_category(c, s, int(m.group(1)), b["category"], _opt(b.get("priority"))); return {"ok": True}
def c_assign(c, s, b, m): complaints.assign_complaint(c, s, int(m.group(1)), int(b["staff_id"])); return {"ok": True}
def c_status(c, s, b, m): complaints.set_status(c, s, int(m.group(1)), b["status"], _opt(b.get("note"))); return {"ok": True}
def c_merge(c, s, b, m): complaints.merge_complaints(c, s, int(m.group(1)), int(b["into_id"])); return {"ok": True}

def guests(c, s, q, m):
    rows = c.execute("SELECT g.*, t.name tenant FROM guests g JOIN tenants t ON t.id=g.tenant_id WHERE g.pg_id=? AND g.status IN('pending','approved') AND g.departure>=? ORDER BY g.status='pending' DESC, g.arrival", (s.pg_id, _today())).fetchall()
    return {"guests": [{"id": g["id"], "guest": g["guest_name"], "tenant": g["tenant"], "room": leave_guests._room(c, g["tenant_id"]), "arrival": _fmt(g["arrival"]), "departure": _fmt(g["departure"]), "nights": g["nights"], "overnight": bool(g["overnight"]), "eats_food": bool(g["eats_food"]),
            "status": g["status"], "reason": g["approval_reason"], "suggested": g["suggested_charge_paise"] is not None and M(g["suggested_charge_paise"])} for g in rows], "leaves": leave_guests.list_leaves(c, s, _today()),
            "max_nights": (c.execute("SELECT guest_max_nights FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone() or [None])[0]}
def g_decide(c, s, b, m): leave_guests.decide_guest(c, s, int(m.group(1)), bool(b.get("approve")), _opt(b.get("note")), _opt(b.get("charge"))); return {"ok": True}

def report(c, s, q, m):
    days = int(m.group(1))
    if days not in (1, 7, 30): raise ValueError("Choose 1, 7 or 30 days")
    now = time.time(); since = now - days * 86400; one = lambda sql, *a: c.execute(sql, (s.pg_id,) + a).fetchone()[0]; month = datetime.now(IST).strftime("%Y-%m")
    charged = one("SELECT COALESCE(SUM(amount_paise),0) FROM charges WHERE pg_id=? AND voided=0 AND kind='rent' AND period=?", month); got = one("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE pg_id=? AND purpose='rent' AND substr(received_date,1,7)=?", month)
    avg = one("SELECT AVG(resolved_at-created)/3600.0 FROM complaints WHERE pg_id=? AND merged_into IS NULL AND resolved_at>=?", since); ov = tenants.room_overview(c, s)
    return {"days": days, "complaints_raised": one("SELECT COUNT(*) FROM complaints WHERE pg_id=? AND merged_into IS NULL AND created>=?", since), "complaints_resolved": one("SELECT COUNT(*) FROM complaints WHERE pg_id=? AND merged_into IS NULL AND resolved_at>=?", since),
            "open_now": complaints.complaint_counts(c, s, now)["open"], "avg_resolution_hours": avg and round(avg, 1), "food_complaints": one("SELECT COUNT(*) FROM complaints x JOIN complaint_categories k ON k.id=x.category_id WHERE x.pg_id=? AND k.name='Food' AND x.created>=?", since),
            "repeat_problems": [f"Room {r['room']}: {r['category']} {r['n']} times (14 days)" for r in assistant.repeat_problems(c, s, now)], "month": month, "rent_charged": M(charged), "rent_received": M(got), "collection_percent": round(100 * got / charged) if charged else None,
            "occupied": ov["occupied"], "total_beds": ov["total_beds"], "vacant": ov["vacant"]}

def settings(c, s, q, m):
    st = c.execute("SELECT * FROM pg_settings WHERE pg_id=?", (s.pg_id,)).fetchone(); lf = c.execute("SELECT * FROM late_fee_config WHERE pg_id=?", (s.pg_id,)).fetchone(); gs = c.execute("SELECT * FROM guest_settings WHERE pg_id=?", (s.pg_id,)).fetchone()
    return {"details": dict(st) if st else {}, "setup": setup.setup_status(c, s), "floors": c.execute("SELECT COUNT(*) FROM floors WHERE pg_id=?", (s.pg_id,)).fetchone()[0], "rooms": c.execute("SELECT COUNT(*) FROM rooms WHERE pg_id=?", (s.pg_id,)).fetchone()[0],
            "staff": [dict(r) for r in c.execute("SELECT id,role,name,phone,language FROM staff_contacts WHERE pg_id=? AND active=1", (s.pg_id,))], "late_fee": lf and {"enabled": bool(lf["enabled"]), "amount": M(lf["amount_paise"]), "grace_days": lf["grace_days"]},
            "guest_policy": {"always_require_approval": bool(gs and gs["always_require_approval"])}, "notices": notices.list_notices(c, s)}
def save_details(c, s, b, m):
    f = {k: _opt(v) for k, v in b.items() if k in setup.PG_FIELDS}
    for k in ("rent_due_day", "guest_max_nights"):
        if f.get(k) is not None: f[k] = int(f[k])
    if "food_available" in f and f["food_available"] is not None: f["food_available"] = int(bool(f["food_available"]))
    setup.update_pg_details(c, s, **f); return {"ok": True}
def floors(c, s, b, m): setup.set_floors(c, s, int(b["count"])); return {"ok": True}
def add_room(c, s, b, m): return {"id": setup.add_room(c, s, int(b["floor"]), str(b["room_number"]).strip(), int(b["capacity"]), b["rent"], b["deposit"], b.get("room_type") or "Standard")}
def add_staff(c, s, b, m): return {"id": setup.add_staff(c, s, b["role"], b["name"].strip(), b["phone"], b.get("language") or "hi")}
def backup_contact(c, s, b, m): setup.set_backup_contact(c, s, b["name"].strip(), b["phone"]); return {"ok": True}
def late_fee(c, s, b, m): payments.configure_late_fee(c, s, b["amount"], int(b["grace_days"]), confirm=bool(b.get("confirm"))); return {"ok": True}
def late_fee_off(c, s, b, m): payments.disable_late_fee(c, s); return {"ok": True}
def guest_policy(c, s, b, m): leave_guests.set_guest_policy(c, s, always_require_approval=bool(b.get("always_require_approval"))); return {"ok": True}
def archive_notice(c, s, b, m): notices.archive_notice(c, s, int(m.group(1))); return {"ok": True}

R = [("GET", r"/api/owner/residents", residents), ("POST", r"/api/owner/residents", add_resident), ("POST", r"/api/owner/residents/(\d+)/invite", resend_invite), ("GET", r"/api/owner/residents/(\d+)", resident),
     ("POST", r"/api/owner/residents/(\d+)/assign", assign), ("POST", r"/api/owner/residents/(\d+)/move", move), ("POST", r"/api/owner/residents/(\d+)/moveout/prepare", moveout_prepare), ("POST", r"/api/owner/moveout/(\d+)/confirm", moveout_confirm),
     ("GET", r"/api/owner/rooms", rooms), ("GET", r"/api/owner/meta", meta), ("GET", r"/api/owner/payments", payments_page), ("POST", r"/api/owner/payments", record_payment), ("POST", r"/api/owner/payments/(\d+)/reverse", reverse_payment),
     ("POST", r"/api/owner/charges/generate", gen_charges), ("POST", r"/api/owner/charges/(\d+)/void", void_charge), ("GET", r"/api/owner/complaints", complaints_list), ("POST", r"/api/owner/complaints/(\d+)/correct", c_correct), ("POST", r"/api/owner/complaints/(\d+)/assign", c_assign),
     ("POST", r"/api/owner/complaints/(\d+)/status", c_status), ("POST", r"/api/owner/complaints/(\d+)/merge", c_merge), ("GET", r"/api/owner/guests", guests), ("POST", r"/api/owner/guests/(\d+)/decide", g_decide),
     ("GET", r"/api/owner/report/(\d+)", report), ("GET", r"/api/owner/settings", settings), ("POST", r"/api/owner/settings", save_details), ("POST", r"/api/owner/floors", floors), ("POST", r"/api/owner/rooms", add_room),
     ("POST", r"/api/owner/staff", add_staff), ("POST", r"/api/owner/backup-contact", backup_contact), ("POST", r"/api/owner/late-fee", late_fee), ("POST", r"/api/owner/late-fee/disable", late_fee_off),
     ("POST", r"/api/owner/guest-policy", guest_policy), ("POST", r"/api/owner/notices/(\d+)/archive", archive_notice)]
ROUTES = [(mth, rx, "owner", fn) for mth, rx, fn in R]
