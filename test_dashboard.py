import unittest
from core import *
import dashboard
from dashboard import *
from complaints import create_complaint, set_status, check_deadlines, merge_complaints
from setup import set_floors, add_room, add_staff, update_pg_details, set_backup_contact
from tenants import add_tenant, assign_bed
from payments import generate_rent_charges, record_payment

H = 3600; T0 = 1_900_000_000.0     # 2030-03-17 (India time)
AI = lambda cat, pr, s=None: (lambda text, cats: {"category": cat, "priority": pr, "summary": s})

class T(unittest.TestCase):
    def setUp(self):
        self.c = dashboard.connect()
        create_pg_with_owner(self.c, "A", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
    def full_pg(self):
        set_floors(self.c, self.o, 3); beds = []
        for rn, fl in (("101", 1), ("204", 2)):
            rid = add_room(self.c, self.o, fl, rn, 2, 6500, 10000); beds += [r[0] for r in self.c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        self.ts = []
        for i, (n, ph) in enumerate((("Ravi", "9876543210"), ("Sam", "9123456789"), ("Anu", "9000000009"))):
            tid, inv = add_tenant(self.c, self.o, n, ph); s = get_session(self.c, accept_invite(self.c, inv, "4821")); assign_bed(self.c, self.o, tid, beds[i + 1 if i else 0], move_in="2030-03-01"); self.ts.append((tid, s))
        add_staff(self.c, self.o, "plumber", "Raj", "9000000001"); add_staff(self.c, self.o, "cook", "Imran", "9000000002")
        set_backup_contact(self.c, self.o, "Meena", "9123456780"); update_pg_details(self.c, self.o, address="MG Rd", phone="9876500000", rent_due_day=5, guest_max_nights=2)

    def test_inr_format(self):
        self.assertEqual([inr(x) for x in (650000, 3200000, 12345678, 0, 99, -250000)], ["₹6,500", "₹32,000", "₹1,23,456.78", "₹0", "₹0.99", "-₹2,500"])

    def test_empty_pg_only_shows_setup(self):
        d = today_dashboard(self.c, self.o, T0); self.assertEqual(d["glance"]["residents"], 0); self.assertEqual([i["kind"] for i in d["needs_attention"]], ["setup"])

    def test_full_picture_order_and_numbers(self):
        self.full_pg(); generate_rent_charges(self.c, self.o, "2030-03"); record_payment(self.c, self.o, self.ts[0][0], 4500, "cash", today="2030-03-10")
        urgent = create_complaint(self.c, self.ts[1][1], "Switch se sparks", AI("Electricity", "LOW"), now=T0 - 3 * H)
        leak = create_complaint(self.c, self.ts[0][1], "Ceiling leak", AI("Plumbing", "HIGH", "Ceiling water leakage"), now=T0 - 16 * H)   # deadline passed 8h ago
        create_complaint(self.c, self.ts[2][1], "Fan slow", AI("AC/Fan", "LOW"), now=T0 - H)                                                   # normal: counted, not listed
        create_complaint(self.c, self.ts[0][1], "Wifi issue", now=T0 - H)                                                                       # AI not connected -> needs review
        for t in self.ts: create_complaint(self.c, t[1], "Dinner was bad", AI("Food", "LOW"), now=T0 - H)
        check_deadlines(self.c, self.o, T0)
        d = today_dashboard(self.c, self.o, T0); g = d["glance"]; items = d["needs_attention"]
        self.assertEqual((g["residents"], g["total_beds"], g["occupied_beds"], g["vacant_beds"], g["food_reports_24h"]), (3, 4, 3, 1, 3))
        self.assertEqual(g["rent_overdue"], "₹15,000"); self.assertEqual(g["urgent_unresolved"], 1)      # 3 x 6500 - 4500
        self.assertEqual(items[0]["complaint_id"], leak)      # oldest overdue first
        sev = [i["severity"] for i in items]; self.assertEqual(sev, sorted(sev, key={"red": 0, "orange": 1, "yellow": 2}.get))
        self.assertTrue(all(i["label"] for i in items))                                                  # text label, never colour alone
        txt = " | ".join(i["text"] for i in items)
        self.assertIn("Room 101 — Ceiling water leakage — 8 hours overdue", txt); self.assertIn("rent overdue from 3 residents", txt)
        self.assertIn("3 residents reported food problems", txt); self.assertIn("needs sorting", txt); self.assertNotIn("Fan slow", txt)
        self.assertIn(urgent, [i.get("complaint_id") for i in items])

    def test_escalation_is_shown_and_clears_when_resolved(self):
        self.full_pg(); a = create_complaint(self.c, self.ts[0][1], "Tap leaking", AI("Plumbing", "HIGH", "Tap leak"), now=T0)
        check_deadlines(self.c, self.o, T0 + 16 * H)
        self.assertIn("backup contact alerted", " ".join(i["text"] for i in today_dashboard(self.c, self.o, T0 + 16 * H)["needs_attention"]))
        set_status(self.c, self.o, a, "resolved", now=T0 + 17 * H)
        self.assertNotIn("Tap leak", " ".join(i["text"] for i in today_dashboard(self.c, self.o, T0 + 17 * H)["needs_attention"]))

    def test_opening_dashboard_changes_nothing(self):
        self.full_pg(); create_complaint(self.c, self.ts[0][1], "Tap leaking", AI("Plumbing", "HIGH"), now=T0); self.c.commit()
        before = self.c.total_changes; today_dashboard(self.c, self.o, T0 + 20 * H); self.assertEqual(self.c.total_changes, before)

    def test_isolation_cap_and_plugins(self):
        self.full_pg(); self.assertEqual(today_dashboard(self.c, self.o2, T0)["glance"]["residents"], 0)
        with self.assertRaises(AuthError): today_dashboard(self.c, self.ts[0][1], T0)
        @provider
        def fake(c, s, now): return [{"severity": "yellow", "label": "GUEST", "kind": "guest", "text": f"guest {i}", "age_hours": 0} for i in range(15)]
        try:
            d = today_dashboard(self.c, self.o, T0); self.assertEqual((len(d["needs_attention"]), d["hidden_items"] > 0, d["all_clear"]), (10, True, False))
        finally: dashboard.PROVIDERS.remove(fake)

if __name__ == "__main__": unittest.main()
