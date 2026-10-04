"""Module 6: owner dashboard - 'What needs my attention today?'. READ-ONLY: opening it never changes anything.
Other modules (leave, guests, notifications) plug in extra attention items with @provider - no rewrite needed."""
from datetime import datetime, timezone, timedelta
import complaints, setup
from core import require_owner
from complaints import OPEN_NOT
from payments import pending_summary
from tenants import room_overview

IST = timezone(timedelta(hours=5, minutes=30))     # single-PG India default; becomes a per-PG setting for multi-PG
FOOD_ALERT_MIN = 3                                 # flagged default: this many residents reporting food in 24h
MAX_ITEMS = 10; SEV = {"red": 0, "orange": 1, "yellow": 2}; PROVIDERS = []

def provider(fn): PROVIDERS.append(fn); return fn
def connect(path=":memory:"): return complaints.connect(path)

def inr(p):
    """Paise -> Indian format: 3200000 -> ₹32,000 ; 12345678 -> ₹1,23,456.78"""
    r, ps = divmod(abs(p), 100); d = str(r)
    if len(d) > 3:
        head, tail, g = d[:-3], d[-3:], []
        while len(head) > 2: g.insert(0, head[-2:]); head = head[:-2]
        d = ",".join(([head] if head else []) + g + [tail])
    return f"{'-' if p < 0 else ''}₹{d}" + (f".{ps:02d}" if ps else "")

def _complaint_items(c, s, now, rows):
    esc = {r[0]: r[1] for r in c.execute("""SELECT x.id, MAX(e.level) FROM escalations e JOIN complaints x ON x.id=e.complaint_id AND e.cycle=x.reopen_count
        WHERE x.pg_id=? AND x.merged_into IS NULL AND x.status NOT IN('resolved','closed') GROUP BY x.id""", (s.pg_id,))}
    out = []
    for r in rows:
        urgent, over, lvl, reop = r["priority"] == "URGENT", r["overdue"], esc.get(r["id"], 0), r["reopen_count"] > 0
        if not (urgent or over or lvl >= 2 or reop): continue
        label = "URGENT" if urgent else "OVERDUE" if over else "ESCALATED" if lvl >= 2 else "REOPENED"
        where = f"Room {r['room_number']}" if r["room_number"] else r["tenant"]
        what = r["summary"] or ((r["category"] or "Needs review") + ": " + r["description"][:50])
        age = round((now - r["deadline"]) / 3600, 1) if over else r["hours_open"]
        hrs = f"{age:g} hour{'' if age == 1 else 's'}"
        txt = f"{where} — {what} — " + (f"{hrs} overdue" if over else f"open {hrs}")
        txt += " — backup contact alerted" if lvl >= 3 else " — escalated to you" if lvl == 2 else ""
        txt += " — resident says not fixed" if reop else ""
        txt += " — needs sorting" if r["status"] == "needs_review" else ""
        out.append({"severity": "red" if (urgent or over or lvl >= 2) else "orange", "label": label, "kind": "complaint", "complaint_id": r["id"], "text": txt, "age_hours": age})
    return out

def today_dashboard(c, s, now=None):
    require_owner(s); now = now or datetime.now(timezone.utc).timestamp(); today = datetime.fromtimestamp(now, IST).date().isoformat()
    ov = room_overview(c, s); rent = pending_summary(c, s, today); cnt = complaints.complaint_counts(c, s, now)
    rows = [r for r in complaints.list_complaints(c, s, now) if r["status"] not in OPEN_NOT]
    residents = c.execute("SELECT COUNT(*) FROM tenants WHERE pg_id=? AND status='active'", (s.pg_id,)).fetchone()[0]
    food = c.execute("""SELECT COUNT(DISTINCT x.tenant_id) FROM complaints x JOIN complaint_categories k ON k.id=x.category_id
        WHERE x.pg_id=? AND k.name='Food' AND x.created>=?""", (s.pg_id, now - 86400)).fetchone()[0]
    items = _complaint_items(c, s, now, rows)
    if rent["total_overdue_paise"]:
        who = [r for r in rent["rows"] if r["overdue_paise"]]
        items.append({"severity": "red", "label": "RENT OVERDUE", "kind": "rent", "text": f"{inr(rent['total_overdue_paise'])} rent overdue from {len(who)} resident{'s' * (len(who) != 1)}",
                      "detail": [{"tenant_id": r["tenant_id"], "name": r["name"], "overdue": inr(r["overdue_paise"])} for r in who], "age_hours": 0})
    nr = sum(r["status"] == "needs_review" and r["priority"] != "URGENT" for r in rows)
    if nr: items.append({"severity": "orange", "label": "NEEDS REVIEW", "kind": "needs_review", "text": f"{nr} complaint{'s' * (nr != 1)} {'needs' if nr == 1 else 'need'} sorting - the AI could not categorise {'it' if nr == 1 else 'them'}", "age_hours": 0})
    if food >= FOOD_ALERT_MIN: items.append({"severity": "orange", "label": "FOOD ALERT", "kind": "food", "text": f"{food} residents reported food problems in the last 24 hours", "age_hours": 0})
    dup = sum(bool(r["possible_duplicate_of"]) for r in rows)
    if dup: items.append({"severity": "yellow", "label": "CHECK", "kind": "duplicates", "text": f"{dup} complaint{'s' * (dup != 1)} may be duplicates - review and merge", "age_hours": 0})
    todo = setup.setup_status(c, s)["todo"]
    if todo: items.append({"severity": "yellow", "label": "SETUP", "kind": "setup", "text": "Finish setup: " + "; ".join(todo), "age_hours": 0})
    for p in PROVIDERS: items += p(c, s, now)
    items.sort(key=lambda i: (SEV[i["severity"]], -i.get("age_hours", 0)))
    return {"as_of": today,
            "glance": {"residents": residents, "total_beds": ov["total_beds"], "occupied_beds": ov["occupied"], "vacant_beds": ov["vacant"],
                       "rent_pending": inr(rent["total_pending_paise"]), "rent_overdue": inr(rent["total_overdue_paise"]),
                       "open_complaints": cnt["open"], "overdue_complaints": cnt["overdue"], "urgent_unresolved": cnt["urgent_unresolved"], "food_reports_24h": food},
            "needs_attention": items[:MAX_ITEMS], "hidden_items": max(len(items) - MAX_ITEMS, 0), "all_clear": not items}
