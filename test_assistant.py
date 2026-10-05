import unittest
from datetime import datetime
from core import *
import assistant, notifications
from assistant import ask, repeat_problems
from complaints import create_complaint, set_status
from setup import set_floors, add_room, update_pg_details
from tenants import add_tenant, assign_bed
from payments import generate_rent_charges, record_payment
H = 3600; T0 = datetime.fromisoformat("2030-03-17T12:00:00+05:30").timestamp()
AI = lambda cat, pr="MEDIUM": (lambda text, cats: {"category": cat, "priority": pr, "summary": "x"})

class T(unittest.TestCase):
    def setUp(self):
        self.c = notifications.connect()
        create_pg_with_owner(self.c, "A", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 3); rid = add_room(self.c, self.o, 2, "204", 2, 6500, 10000); beds = [r[0] for r in self.c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        self.t1, self.s1 = self.tenant("Ravi", "9876543210", beds[0]); self.t2, self.s2 = self.tenant("Sam", "9123456789", beds[1]); update_pg_details(self.c, self.o, rent_due_day=5)
    def tenant(self, n, ph, bed):
        tid, inv = add_tenant(self.c, self.o, n, ph); s = get_session(self.c, accept_invite(self.c, inv, "4821")); assign_bed(self.c, self.o, tid, bed, move_in="2030-03-01"); return tid, s
    def a(self, s, q): return ask(self.c, s, q, T0)["answer"]

    def test_tenant_rent_answers_match_records_and_hinglish(self):
        self.assertIn("No rent has been charged", self.a(self.s1, "When is my rent due?"))                    # no data -> says so, invents nothing
        generate_rent_charges(self.c, self.o, "2030-03"); record_payment(self.c, self.o, self.t1, 4000, "cash", today="2030-03-04")
        self.assertEqual(self.a(self.s1, "When is my rent due?"), "Your rent of ₹6,500 is due on 5 Mar.")
        self.assertIn("You have paid ₹4,000. ₹2,500 remains", self.a(self.s1, "How much have I paid?")); self.assertIn("₹2,500 remains", self.a(self.s1, "rent kitna baki hai"))
        self.assertIn("₹6,500. You have paid ₹0", self.a(self.s2, "how much have I paid"))                       # another tenant's payment never leaks
        self.assertIn("Room 204, Bed A, Floor 2", self.a(self.s1, "which room am I in")); self.assertIn("Paid so far: ₹0 (not paid)", self.a(self.s1, "deposit status"))

    def test_problem_is_proposed_not_created(self):
        before = self.c.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]; r = ask(self.c, self.s1, "There's water leaking in my bathroom", T0)
        self.assertEqual(r["action"]["type"], "create_complaint"); self.assertEqual(self.c.execute("SELECT COUNT(*) FROM complaints").fetchone()[0], before)
        cid = create_complaint(self.c, self.s1, r["action"]["text"], AI("Plumbing", "HIGH"), now=T0)                    # the tap
        self.assertIn(f"#{cid}", self.a(self.s1, "my complaint status")); self.assertIn("no complaints", self.a(self.s2, "my complaint status"))
        set_status(self.c, self.o, cid, "resolved", now=T0 + H); r = ask(self.c, self.s1, "still not fixed", T0 + 2 * H); self.assertEqual(r["action"], {"type": "confirm_fixed", "complaint_id": cid, "fixed": False})

    def test_refuses_sensitive_and_unknown_questions_honestly(self):
        for q in ("Please reduce my rent", "refund my deposit", "mark paid"): self.assertIn("can't do that", self.a(self.s1, q)); self.assertIsNone(ask(self.c, self.s1, q, T0)["action"])
        self.assertIn("I can't answer that from your records", self.a(self.s1, "who will win the match")); self.assertIn("I can't answer that", self.a(self.o, "what's the weather"))
        self.assertIn("won't make money", self.a(self.o, "waive the late fee for Ravi"))
        with self.assertRaises(ValueError): ask(self.c, self.s1, "  ")

    def test_owner_answers_from_real_counts(self):
        self.assertEqual(self.a(self.o, "How many complaints are unresolved?"), "There are 0 unresolved complaints. 0 are overdue.")
        for i in range(3): create_complaint(self.c, self.s1, f"Ceiling leak {i}", AI("Plumbing", "HIGH"), now=T0 - i * 24 * H)
        create_complaint(self.c, self.s2, "Fan", AI("AC/Fan"), now=T0)
        self.assertIn("There are 4 unresolved complaints. 2 are overdue.", self.a(self.o, "How many complaints are unresolved?"))
        self.assertEqual(self.a(self.o, "Which rooms have repeated problems?"), "Room 204 has reported Plumbing 3 times in the last 14 days.")
        generate_rent_charges(self.c, self.o, "2030-03"); self.assertIn("Rent pending is ₹13,000, of which ₹13,000 is overdue", self.a(self.o, "rent pending?")); self.assertIn("Ravi ₹6,500", self.a(self.o, "who owes rent"))
        self.assertEqual(self.a(self.o, "how many vacant beds"), "2 of 2 beds are occupied. 0 are vacant.")

    def test_isolation_and_old_repeats_not_counted(self):
        for i in range(3): create_complaint(self.c, self.s1, "leak", AI("Plumbing"), now=T0 - 30 * 86400 - i)
        self.assertEqual(repeat_problems(self.c, self.o, T0), []); self.assertEqual(self.a(self.o2, "how many complaints unresolved"), "There are 0 unresolved complaints. 0 are overdue.")
        self.assertIn("0 of 0 beds", self.a(self.o2, "vacant beds")); self.assertEqual(self.o2.pg_id != self.o.pg_id, True)
        with self.assertRaises(AuthError): repeat_problems(self.c, self.s1)

if __name__ == "__main__": unittest.main()
