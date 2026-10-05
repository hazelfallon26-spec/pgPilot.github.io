"""Module 9: AI assistant. It ANSWERS ONLY FROM REAL RECORDS: every sentence below is built from database figures, never from free-form AI text.
Questions are matched by simple English/Hinglish keyword rules (works with no AI service). An AI model can later be added only to pick the question type - never to write facts.
It is read-only: anything that changes data is returned as a proposed action the person must tap to confirm."""
import re, time
import complaints, payments, tenants, leave_guests
from complaints import _me
from core import AuthError, require_owner
from dashboard import inr
from leave_guests import _fmt

R_VERBS = ("reduce", "lower", "change", "waive", "delete", "remove", "edit", "increase", "adjust", "mark", "approve", "refund", "discount", "erase")
R_OBJ = ("rent", "deposit", "fee", "charge", "paid", "payment", "dues", "guest", "tenant", "resident", "record", "balance", "amount", "data")
HELP_T = "I can tell you your rent due date, how much you've paid or still owe, your deposit, your room, and the status of your complaints. To report a problem, just describe it."
HELP_O = "I can tell you about unresolved complaints, repeated problems, rent pending, who owes rent, and vacant beds."
PROBLEM = ("leak", "not working", "broken", "dirty", "unclean", "bad", "stale", "no water", "no electricity", "paani", "kharab", "gandh", "band hai", "tut", "wifi", "wi-fi", "internet", "noise", "complaint", "problem", "smell", "spark", "shock", "fire")
def has(t, *words):
    """Whole-word matching (so 'bathroom' is not 'room'); words of 4+ letters also match their endings ('leak' -> 'leaking'); phrases match as text."""
    toks = re.findall(r"[a-z0-9'-]+", t)
    return any((w in t) if " " in w else any(k == w or (len(w) >= 4 and k.startswith(w)) for k in toks) for w in words)
def refuse(t): return (has(t, *R_VERBS) and has(t, *R_OBJ)) or has(t, "refund", "discount", "rent kam")

def repeat_problems(c, s, now=None, days=14, minimum=3):
    """Simple count only: same room + same category, at least `minimum` times in `days` days. (Complaints still in 'Needs review' have no category yet, so are not counted.)"""
    require_owner(s); now = now or time.time()
    return [dict(r) for r in c.execute("""SELECT x.room_number room, k.name category, COUNT(*) n FROM complaints x JOIN complaint_categories k ON k.id=x.category_id
        WHERE x.pg_id=? AND x.merged_into IS NULL AND x.room_number IS NOT NULL AND x.created>=? GROUP BY x.room_number, k.name HAVING COUNT(*)>=? ORDER BY n DESC""", (s.pg_id, now - days * 86400, minimum))]

def _tenant_answer(c, s, raw, t):
    if refuse(t): return {"answer": "I can't do that. Only the PG owner can change money or tenant records. I can help you contact them if you like.", "action": None}
    if has(t, "still not fixed", "not fixed", "abhi bhi", "theek nahi", "thik nahi"):
        w = [x for x in complaints.my_complaints(c, s) if x["needs_my_confirmation"]]
        return {"answer": f"Please tap 'No, still a problem' on complaint #{w[0]['id']} and I'll reopen it." if w else "I don't see a complaint waiting for your confirmation. Describe the problem and I'll report it again.",
                "action": {"type": "confirm_fixed", "complaint_id": w[0]["id"], "fixed": False} if w else None}
    if has(t, "complaint status", "my complaint", "meri complaint", "complaint kya", "status of"):
        cs = complaints.my_complaints(c, s)
        return {"answer": "You have no complaints on record." if not cs else "Your complaints: " + "; ".join(f"#{x['id']} {x['text'][:40]} — {x['status']}" for x in cs[:5]), "action": None}
    if has(t, "deposit"):
        d = payments.my_payments(c, s)["deposit"]; return {"answer": f"Security deposit: {inr(d['expected_paise'])}. Paid so far: {inr(d['paid_paise'])} ({d['status']}).", "action": None}
    if has(t, "room", "bed", "floor", "kamra"):
        p = tenants.my_profile(c, s); return {"answer": f"You are in Room {p['room']}, Bed {p['bed']}, Floor {p['floor']}." if p["room"] else "A bed has not been assigned to you yet.", "action": None}
    if has(t, "rent", "due", "paid", "pay", "baki", "baaki", "pending", "kitna", "kab", "owe", "remaining", "payment"):
        m = payments.my_payments(c, s); cur = m["current"]
        if not cur: return {"answer": "No rent has been charged to you yet. Please ask the PG owner.", "action": None}
        if has(t, "due", "when", "kab", "date"): return {"answer": f"Your rent of {inr(cur['amount_paise'])} is due on {_fmt(cur['due_date'])}.", "action": None}
        return {"answer": f"Rent: {inr(cur['amount_paise'])}. You have paid {inr(cur['paid_paise'])}. " + (f"{inr(cur['remaining_paise'])} remains." if cur["remaining_paise"] else "Nothing remains.") + f" Due date: {_fmt(cur['due_date'])}.", "action": None}
    if has(t, *PROBLEM):
        return {"answer": f"I can report this to the PG for you: \"{raw[:120]}\". Tap Confirm to send it.", "action": {"type": "create_complaint", "text": raw}}   # nothing is created until the tenant taps
    return {"answer": "I can't answer that from your records. " + HELP_T, "action": None}

def _owner_answer(c, s, raw, t, now):
    if refuse(t): return {"answer": "I won't make money or tenant-record changes on my own. Please do that yourself in Payments or Residents, where it is logged.", "action": None}
    if has(t, "repeat", "recurring", "same problem", "again"):
        r = repeat_problems(c, s, now); return {"answer": "No room has 3 or more complaints of the same type in the last 14 days." if not r else " ".join(f"Room {x['room']} has reported {x['category']} {x['n']} times in the last 14 days." for x in r[:5]), "action": None}
    if has(t, "complaint", "unresolved", "open issue"):
        n = complaints.complaint_counts(c, s, now); return {"answer": f"There are {n['open']} unresolved complaints. {n['overdue']} are overdue." + (f" {n['needs_review']} need sorting." if n["needs_review"] else ""), "action": None}
    if has(t, "who owe", "who has not paid", "kisne", "defaulter", "owes"):
        p = payments.pending_summary(c, s, time.strftime("%Y-%m-%d", time.gmtime(now))); return {"answer": "Nobody has rent pending." if not p["rows"] else "Rent pending: " + "; ".join(f"{x['name']} {inr(x['pending_paise'])}" for x in p["rows"][:8]), "action": None}
    if has(t, "rent", "pending", "collection"):
        p = payments.pending_summary(c, s, time.strftime("%Y-%m-%d", time.gmtime(now))); return {"answer": f"Rent pending is {inr(p['total_pending_paise'])}, of which {inr(p['total_overdue_paise'])} is overdue.", "action": None}
    if has(t, "vacant", "vacancy", "empty", "occupancy", "beds"):
        o = tenants.room_overview(c, s); return {"answer": f"{o['occupied']} of {o['total_beds']} beds are occupied. {o['vacant']} are vacant.", "action": None}
    return {"answer": "I can't answer that from your records. " + HELP_O, "action": None}

def ask(c, s, text, now=None):
    raw = " ".join((text or "").split())
    if not raw: raise ValueError("Please type a question")
    t = raw.lower(); now = now or time.time()
    return _tenant_answer(c, s, raw, t) if s.role == "tenant" else _owner_answer(c, s, raw, t, now)
