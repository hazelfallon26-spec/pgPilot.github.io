"""Module 5: complaints. Tenant writes in plain words -> saved FIRST -> AI classifies -> rules enforce safety -> owner stays in control.
AI is pluggable: nothing here calls a real AI until a classifier is connected (then complaints go to 'Needs review', never lost)."""
import json, re, time
import payments
from core import audit, require_owner, AuthError
from tenants import TenantError

SCHEMA = """
CREATE TABLE IF NOT EXISTS complaint_categories(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, name TEXT NOT NULL, staff_role TEXT, active INTEGER DEFAULT 1, UNIQUE(pg_id,name));
CREATE TABLE IF NOT EXISTS sla_hours(pg_id INTEGER NOT NULL, priority TEXT NOT NULL, hours REAL NOT NULL CHECK(hours>0), PRIMARY KEY(pg_id,priority));
CREATE TABLE IF NOT EXISTS complaints(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, room_number TEXT, floor_number INTEGER,
  category_id INTEGER, description TEXT NOT NULL, summary TEXT, priority TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, sla_hours REAL, deadline REAL,
  assigned_staff_id INTEGER, ai_state TEXT, ai_category TEXT, keyword_forced INTEGER DEFAULT 0, possible_duplicate_of INTEGER, merged_into INTEGER,
  acknowledged_at REAL, resolved_at REAL, tenant_confirmed INTEGER, reopen_count INTEGER DEFAULT 0, idem_key TEXT, UNIQUE(pg_id,idem_key));
CREATE TABLE IF NOT EXISTS complaint_updates(id INTEGER PRIMARY KEY, pg_id INTEGER NOT NULL, complaint_id INTEGER NOT NULL, ts REAL, actor_id INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS escalations(complaint_id INTEGER NOT NULL, cycle INTEGER NOT NULL, level INTEGER NOT NULL, ts REAL, UNIQUE(complaint_id,cycle,level));
"""
PRIORITIES = ["LOW", "MEDIUM", "HIGH", "URGENT"]; RANK = {p: i for i, p in enumerate(PRIORITIES)}
DEFAULT_SLA = {"URGENT": 2, "HIGH": 8, "MEDIUM": 24, "LOW": 72}      # hours - flagged defaults, owner can change
DEFAULT_CATS = [("Maintenance", "maintenance"), ("Plumbing", "plumber"), ("Electricity", "electrician"), ("AC/Fan", "electrician"), ("Water", "plumber"),
                ("Cleanliness", "cleaner"), ("Food", "cook"), ("Wi-Fi/Internet", None), ("Room/Furniture", "maintenance"), ("Security", None), ("Noise", None), ("Other", None)]
SHARED = {"Food", "Wi-Fi/Internet", "Water", "Noise"}                  # problems several residents can report separately
DANGER = re.compile(r"\b(spark(s|ing|ed)?|shock(ed)?|short[ -]?circuit|fire|aag|smoke|dhuan|burning smell|gas (leak|smell)|leaking gas|electrocut\w*|current lag\w*|flood(ing|ed)?|ceiling (fell|collapse\w*))\b", re.I)
OPEN_NOT = ("resolved", "closed")
TENANT_LABEL = {"new": "Reported", "needs_review": "Reported", "acknowledged": "Reported", "assigned": "Being handled", "in_progress": "Being handled",
                "waiting": "Being handled", "resolved": "Resolved - please confirm", "closed": "Confirmed"}
URGENT_ACK_SECS = 30 * 60; AUTO_CLOSE_HOURS = 72                       # flagged defaults
class ComplaintError(TenantError): pass

def connect(path=":memory:"):
    c = payments.connect(path); c.executescript(SCHEMA); return c

def ensure_defaults(c, pg_id):
    for n, r in DEFAULT_CATS: c.execute("INSERT OR IGNORE INTO complaint_categories(pg_id,name,staff_role) VALUES(?,?,?)", (pg_id, n, r))
    for p, h in DEFAULT_SLA.items(): c.execute("INSERT OR IGNORE INTO sla_hours VALUES(?,?,?)", (pg_id, p, h))
    c.commit()
def _sla(c, pg, p): return c.execute("SELECT hours FROM sla_hours WHERE pg_id=? AND priority=?", (pg, p)).fetchone()[0]
def _cat(c, pg, name): return c.execute("SELECT * FROM complaint_categories WHERE pg_id=? AND name=? AND active=1", (pg, name)).fetchone()
def _log(c, s, cid, text, now): c.execute("INSERT INTO complaint_updates(pg_id,complaint_id,ts,actor_id,text) VALUES(?,?,?,?,?)", (s.pg_id, cid, now, s.user_id, text))
def _staff_for(c, pg, role):
    r = c.execute("SELECT id FROM staff_contacts WHERE pg_id=? AND role=? AND active=1 ORDER BY id LIMIT 1", (pg, role)).fetchone() if role else None
    return r[0] if r else None
def _me(c, s):
    if s.role != "tenant": raise AuthError("Tenant only")
    ph = c.execute("SELECT phone FROM users WHERE id=? AND pg_id=?", (s.user_id, s.pg_id)).fetchone()
    t = c.execute("SELECT id FROM tenants WHERE pg_id=? AND phone=? AND status!='moved_out'", (s.pg_id, ph["phone"])).fetchone()
    if not t: raise AuthError("Not found")
    return t["id"]
def _get(c, s, cid):
    r = c.execute("SELECT * FROM complaints WHERE id=? AND pg_id=?", (cid, s.pg_id)).fetchone()
    if not r: raise AuthError("Not found")
    return r

# ---- AI plug-in point (no real AI connected here: the deployer supplies llm_call(prompt)->text) ----
def build_prompt(text, cats):
    return ("You sort complaints for an Indian PG. The resident may write English, Hindi or Hinglish.\nCategories: " + ", ".join(cats) +
            "\nReply with ONLY JSON: {\"category\": <one category>, \"priority\": LOW|MEDIUM|HIGH|URGENT, \"summary\": <short English>}.\n"
            "Do not mark everything urgent. URGENT only for safety risks.\nComplaint: " + text)
def make_llm_classifier(llm_call):
    def classify(text, cats):
        r = json.loads(llm_call(build_prompt(text, cats)))
        if r.get("category") not in cats or str(r.get("priority", "")).upper() not in RANK: raise ValueError("unusable AI reply")
        return {"category": r["category"], "priority": r["priority"].upper(), "summary": str(r.get("summary", ""))[:200]}
    return classify

def create_complaint(c, s, text, classifier=None, now=None, idem_key=None):
    tid = _me(c, s); text = (text or "").strip(); now = now or time.time()
    if not 3 <= len(text) <= 2000: raise ComplaintError("Please describe the problem (up to 2000 characters)")
    ensure_defaults(c, s.pg_id)
    if idem_key:
        old = c.execute("SELECT id FROM complaints WHERE pg_id=? AND idem_key=?", (s.pg_id, idem_key)).fetchone()
        if old: return old[0]                                          # retry on bad internet: same complaint, not a copy
    loc = c.execute("SELECT r.room_number rn, f.number fl FROM stays st JOIN beds b ON b.id=st.bed_id JOIN rooms r ON r.id=b.room_id JOIN floors f ON f.id=r.floor_id WHERE st.tenant_id=? AND st.end_date IS NULL", (tid,)).fetchone()
    forced = bool(DANGER.search(text)); pr = "URGENT" if forced else "MEDIUM"; sla = _sla(c, s.pg_id, pr)
    cid = c.execute("INSERT INTO complaints(pg_id,tenant_id,room_number,floor_number,description,priority,status,created,sla_hours,deadline,ai_state,keyword_forced,idem_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s.pg_id, tid, loc and loc["rn"], loc and loc["fl"], text, pr, "needs_review", now, sla, now + sla * 3600, "not_connected", int(forced), idem_key)).lastrowid
    c.commit()                                                          # SAVED before the AI is called: it can never be lost
    names = [r["name"] for r in c.execute("SELECT name FROM complaint_categories WHERE pg_id=? AND active=1", (s.pg_id,))]
    state, res = "not_connected", None
    if classifier:
        try: res = classifier(text, names); assert res["category"] in names and res["priority"] in RANK; state = "ok"
        except Exception: state, res = "unavailable", None
    cat = _cat(c, s.pg_id, res["category"]) if res else None
    if res:
        pr = "URGENT" if forced else res["priority"]; staff = _staff_for(c, s.pg_id, cat["staff_role"]); sla = _sla(c, s.pg_id, pr)
        c.execute("UPDATE complaints SET category_id=?, ai_category=?, summary=?, priority=?, sla_hours=?, deadline=?, assigned_staff_id=?, status=? WHERE id=?",
                  (cat["id"], res["category"], res.get("summary"), pr, sla, now + sla * 3600, staff, "assigned" if staff else "new", cid))
        dup = c.execute("""SELECT id FROM complaints WHERE pg_id=? AND category_id=? AND id!=? AND merged_into IS NULL AND status NOT IN('resolved','closed') AND created>=?
                           AND (tenant_id=? OR (room_number IS NOT NULL AND room_number=?) OR ?) ORDER BY id LIMIT 1""",
                        (s.pg_id, cat["id"], cid, now - 86400, tid, loc and loc["rn"], int(res["category"] in SHARED))).fetchone()
        if dup: c.execute("UPDATE complaints SET possible_duplicate_of=? WHERE id=?", (dup[0], cid))
    c.execute("UPDATE complaints SET ai_state=? WHERE id=?", (state, cid))
    audit(c, s.pg_id, s.user_id, "complaint.created", "complaint", cid, {"ai": state, "priority": pr, "danger_keyword": forced}); c.commit(); return cid

def correct_category(c, s, cid, category, priority=None, now=None):
    """One-tap owner fix (also how a 'Needs review' complaint gets sorted). Original AI answer is kept for the record."""
    require_owner(s); x = _get(c, s, cid); now = now or time.time(); cat = _cat(c, s.pg_id, category)
    if not cat: raise ComplaintError("Unknown category")
    pr = priority or x["priority"]
    if pr not in RANK: raise ComplaintError("Bad priority")
    staff = x["assigned_staff_id"] if x["status"] in ("in_progress", "waiting") else _staff_for(c, s.pg_id, cat["staff_role"])
    st = x["status"] if x["status"] not in ("needs_review", "new") else ("assigned" if staff else "new"); sla = _sla(c, s.pg_id, pr)
    c.execute("UPDATE complaints SET category_id=?, priority=?, sla_hours=?, deadline=?, assigned_staff_id=?, status=?, acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
              (cat["id"], pr, sla, x["created"] + sla * 3600, staff, st, now, cid))
    audit(c, s.pg_id, s.user_id, "complaint.corrected", "complaint", cid, {"ai_said": x["ai_category"], "category": category, "priority": [x["priority"], pr]}); c.commit()

def assign_complaint(c, s, cid, staff_id, now=None):
    require_owner(s); x = _get(c, s, cid)
    if not c.execute("SELECT 1 FROM staff_contacts WHERE id=? AND pg_id=? AND active=1", (staff_id, s.pg_id)).fetchone(): raise AuthError("Not found")
    if x["category_id"] is None: raise ComplaintError("Choose a category first")
    c.execute("UPDATE complaints SET assigned_staff_id=?, status=CASE WHEN status IN('new','acknowledged') THEN 'assigned' ELSE status END, acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?", (staff_id, now or time.time(), cid))
    audit(c, s.pg_id, s.user_id, "complaint.assigned", "complaint", cid, {"staff_id": staff_id}); c.commit()

def set_status(c, s, cid, status, note=None, now=None):
    require_owner(s); x = _get(c, s, cid); now = now or time.time()
    if status not in ("acknowledged", "assigned", "in_progress", "waiting", "resolved"): raise ComplaintError("Not allowed. A complaint is closed by the tenant's confirmation")
    if x["category_id"] is None: raise ComplaintError("Choose a category first (Needs review)")
    if x["status"] == "closed" or x["merged_into"]: raise ComplaintError("This complaint is closed")
    c.execute("UPDATE complaints SET status=?, acknowledged_at=COALESCE(acknowledged_at,?), resolved_at=? WHERE id=?", (status, now, now if status == "resolved" else None, cid))
    if note: _log(c, s, cid, note, now)
    audit(c, s.pg_id, s.user_id, "complaint.status", "complaint", cid, {"from": x["status"], "to": status}); c.commit()

def merge_complaints(c, s, dup_id, into_id, now=None):
    """Owner decides. The merged resident is still told the outcome and can still say 'not fixed'."""
    require_owner(s); d, p = _get(c, s, dup_id), _get(c, s, into_id)
    if dup_id == into_id or d["merged_into"] or p["merged_into"] or p["status"] == "closed": raise ComplaintError("These cannot be merged")
    c.execute("UPDATE complaints SET merged_into=?, status='closed' WHERE id=?", (into_id, dup_id)); _log(c, s, into_id, f"Merged duplicate report from room {d['room_number']}", now or time.time())
    audit(c, s.pg_id, s.user_id, "complaint.merged", "complaint", dup_id, {"into": into_id}); c.commit()

def tenant_confirm(c, s, cid, fixed, now=None):
    tid = _me(c, s); x = _get(c, s, cid); now = now or time.time()
    rep = x["tenant_id"] == tid or c.execute("SELECT 1 FROM complaints WHERE merged_into=? AND tenant_id=?", (cid, tid)).fetchone()
    if not rep: raise AuthError("Not found")
    if x["status"] != "resolved": raise ComplaintError("This complaint is not waiting for your confirmation")
    if fixed: c.execute("UPDATE complaints SET status='closed', tenant_confirmed=1 WHERE id=?", (cid,))
    else:                                                               # reopen, bump priority one step (max HIGH; only the owner sets URGENT)
        pr = x["priority"] if x["priority"] == "URGENT" else PRIORITIES[min(RANK[x["priority"]] + 1, 2)]; sla = _sla(c, s.pg_id, pr)
        c.execute("UPDATE complaints SET status='new', tenant_confirmed=0, resolved_at=NULL, reopen_count=reopen_count+1, priority=?, sla_hours=?, deadline=?, acknowledged_at=NULL WHERE id=?", (pr, sla, now + sla * 3600, cid))
        _log(c, s, cid, "Resident says the problem is NOT fixed", now)
    audit(c, s.pg_id, s.user_id, "complaint.confirmed" if fixed else "complaint.reopened", "complaint", cid); c.commit()

def auto_close_unconfirmed(c, s, now=None):
    """If the tenant never answers, close after 72h (flagged default) - the closure is recorded as 'not confirmed by tenant'."""
    require_owner(s); now = now or time.time()
    ids = [r[0] for r in c.execute("SELECT id FROM complaints WHERE pg_id=? AND status='resolved' AND resolved_at<=?", (s.pg_id, now - AUTO_CLOSE_HOURS * 3600))]
    for i in ids: c.execute("UPDATE complaints SET status='closed' WHERE id=?", (i,)); audit(c, s.pg_id, s.user_id, "complaint.auto_closed", "complaint", i)
    c.commit(); return ids

def check_deadlines(c, s, now=None):
    """Returns the reminders/escalations that are due (each fires once per cycle). The notification module delivers them and logs 'not delivered' on failure.
    L1 at deadline -> staff (or owner if unassigned); L2 at deadline+25% of SLA -> owner; L3 -> backup contact if the owner never touched it
    (after deadline+100% of SLA, or URGENT untouched for 30 min). Thresholds are flagged defaults."""
    require_owner(s); now = now or time.time(); out = []
    for x in c.execute("SELECT * FROM complaints WHERE pg_id=? AND merged_into IS NULL AND status NOT IN('resolved','closed')", (s.pg_id,)).fetchall():
        over = now - x["deadline"]; sla = x["sla_hours"] * 3600; due = []
        if over >= 0: due.append((1, "staff" if x["assigned_staff_id"] else "owner"))
        if over >= 0.25 * sla: due.append((2, "owner"))
        if x["acknowledged_at"] is None and (over >= sla or (x["priority"] == "URGENT" and now - x["created"] >= URGENT_ACK_SECS)): due.append((3, "backup"))
        for lvl, to in due:
            if c.execute("INSERT OR IGNORE INTO escalations VALUES(?,?,?,?)", (x["id"], x["reopen_count"], lvl, now)).rowcount:
                out.append({"complaint_id": x["id"], "level": lvl, "to": to, "priority": x["priority"], "hours_overdue": round(max(over, 0) / 3600, 1)})
    c.commit(); return out

def list_complaints(c, s, now=None, category=None, room=None, floor=None, priority=None, status=None, tenant_id=None, since=None, until=None, open_only=False):
    require_owner(s); now = now or time.time(); q = ["x.pg_id=?", "x.merged_into IS NULL"]; a = [s.pg_id]
    for col, v in (("k.name", category), ("x.room_number", room), ("x.floor_number", floor), ("x.priority", priority), ("x.status", status), ("x.tenant_id", tenant_id)):
        if v is not None: q.append(f"{col}=?"); a.append(v)
    if since: q.append("x.created>=?"); a.append(since)
    if until: q.append("x.created<=?"); a.append(until)
    if open_only: q.append("x.status NOT IN('resolved','closed')")
    rows = c.execute(f"SELECT x.*, k.name category, t.name tenant FROM complaints x LEFT JOIN complaint_categories k ON k.id=x.category_id JOIN tenants t ON t.id=x.tenant_id WHERE {' AND '.join(q)}", a).fetchall()
    out = [dict(r, hours_open=round((now - r["created"]) / 3600, 1), overdue=r["status"] not in OPEN_NOT and now > r["deadline"]) for r in rows]
    return sorted(out, key=lambda r: (-RANK[r["priority"]], r["created"]))

def complaint_counts(c, s, now=None):
    """Dashboard feed."""
    rows = list_complaints(c, s, now); op = [r for r in rows if r["status"] not in OPEN_NOT]
    return {"new": sum(r["status"] == "new" for r in op), "needs_review": sum(r["status"] == "needs_review" for r in op), "open": len(op),
            "in_progress": sum(r["status"] in ("assigned", "in_progress", "waiting", "acknowledged") for r in op), "overdue": sum(r["overdue"] for r in op),
            "urgent_unresolved": sum(r["priority"] == "URGENT" for r in op), "reopened": sum(r["reopen_count"] > 0 and r["status"] not in OPEN_NOT for r in rows),
            "awaiting_tenant": sum(r["status"] == "resolved" for r in rows), "possible_duplicates": sum(bool(r["possible_duplicate_of"]) for r in op)}

def my_complaints(c, s):
    """Tenant sees only their own, in the 4 simple steps. Merged reports show the main complaint's progress."""
    tid = _me(c, s); out = []
    for r in c.execute("SELECT x.*, k.name category FROM complaints x LEFT JOIN complaint_categories k ON k.id=x.category_id WHERE x.pg_id=? AND x.tenant_id=? ORDER BY x.id DESC", (s.pg_id, tid)):
        main = _get(c, s, r["merged_into"]) if r["merged_into"] else r
        out.append({"id": main["id"] if r["merged_into"] else r["id"], "text": r["description"], "category": r["category"], "status": TENANT_LABEL[main["status"]], "needs_my_confirmation": main["status"] == "resolved"})
    return out
