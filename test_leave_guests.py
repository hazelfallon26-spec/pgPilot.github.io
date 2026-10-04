import unittest
from datetime import datetime
from core import *
import leave_guests, dashboard
from leave_guests import *
from setup import set_floors, add_room, update_pg_details
from tenants import add_tenant, assign_bed
from payments import generate_rent_charges, tenant_ledger

TODAY = "2030-03-17"; NOW = datetime.fromisoformat("2030-03-17T12:00:00+05:30").timestamp()
class T(unittest.TestCase):
    def setUp(self):
        self.c = leave_guests.connect()
        create_pg_with_owner(self.c, "A", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 3); rid = add_room(self.c, self.o, 2, "204", 2, 6500, 10000); beds = [r[0] for r in self.c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        self.t1, self.s1 = self.tenant("Ravi", "9876543210", beds[0]); self.t2, self.s2 = self.tenant("Sam", "9123456789", beds[1])
        update_pg_details(self.c, self.o, rent_due_day=5, address="x", phone="9000000000")
    def tenant(self, n, ph, bed):
        tid, inv = add_tenant(self.c, self.o, n, ph); s = get_session(self.c, accept_invite(self.c, inv, "4821")); assign_bed(self.c, self.o, tid, bed, move_in="2030-03-01"); return tid, s
    def guest(self, s=None, **k):
        a = dict(guest_name="Rahul", arrival="2030-03-20", departure="2030-03-22", overnight=True, today=TODAY, now=NOW); a.update(k); return create_guest_request(self.c, s or self.s1, **a)
    def att(self): return " | ".join(i["text"] for i in dashboard.today_dashboard(self.c, self.o, NOW)["needs_attention"])

    def test_leave_flow_validation_and_no_money_effect(self):
        generate_rent_charges(self.c, self.o, "2030-03"); before = tenant_ledger(self.c, self.o, self.t1)["pending_paise"]
        lid = create_leave(self.c, self.s1, "2030-10-10", "2030-10-15", "Diwali", today=TODAY, idem_key="k", now=NOW); self.assertEqual(lid, create_leave(self.c, self.s1, "2030-10-10", "2030-10-15", today=TODAY, idem_key="k"))
        self.assertEqual(list_leaves(self.c, self.o, TODAY)[0]["text"], "Away: 10 Oct → 15 Oct"); self.assertEqual(tenant_ledger(self.c, self.o, self.t1)["pending_paise"], before)
        for a in (("2030-03-01", None), ("2030-10-20", "2030-10-18"), ("2030-10-12", "2030-10-20")):
            with self.assertRaises(RequestError): create_leave(self.c, self.s1, a[0], a[1], today=TODAY)
        self.assertIn("will be away 10 Oct → 15 Oct", self.att())
        with self.assertRaises(AuthError): cancel_leave(self.c, self.s2, lid, TODAY)                    # roommate cannot touch it
        cancel_leave(self.c, self.s1, lid, TODAY, now=NOW); self.assertEqual(list_leaves(self.c, self.o, TODAY), []); self.assertIn("cancelled leave", self.att())
        with self.assertRaises(RequestError): cancel_leave(self.c, self.s1, lid, TODAY)

    def test_leave_early_return_and_open_ended(self):
        lid = create_leave(self.c, self.s1, TODAY, None, today=TODAY, now=NOW); self.assertTrue(list_leaves(self.c, self.o, TODAY)[0]["away_today"]); self.assertIn("return date not given", list_leaves(self.c, self.o, TODAY)[0]["text"])
        cancel_leave(self.c, self.s1, lid, TODAY, now=NOW); self.assertEqual(my_leaves(self.c, self.s1)[0]["status"], "returned_early"); self.assertIn("came back early", self.att())

    def test_guest_limit_rules(self):
        self.assertEqual(self.guest()["reason"], "limit_not_set")                                         # no limit configured -> never silently allowed
        self.assertEqual(self.guest(overnight=False, arrival="2030-03-20", departure="2030-03-20", guest_name="Day")["status"], "approved")
        update_pg_details(self.c, self.o, guest_max_nights=2)
        self.assertEqual(self.guest(guest_name="Amit")["status"], "approved")
        r = self.guest(guest_name="Bina", departure="2030-03-25"); self.assertEqual((r["status"], r["reason"]), ("pending", "over_limit")); self.assertIn("2 night(s); this stay is 5", r["message"])
        set_guest_policy(self.c, self.o, always_require_approval=True); self.assertEqual(self.guest(guest_name="Carl")["reason"], "policy")
        for bad in (dict(arrival="2030-03-01"), dict(departure="2030-03-20"), dict(guest_name=" ")):
            with self.assertRaises(RequestError): self.guest(**bad)

    def test_extension_cannot_dodge_limit(self):
        update_pg_details(self.c, self.o, guest_max_nights=2); self.assertEqual(self.guest()["status"], "approved")
        r = self.guest(arrival="2030-03-22", departure="2030-03-24"); self.assertEqual((r["status"], r["reason"]), ("pending", "over_limit"))

    def test_owner_decision_and_charges_only_when_owner_enters(self):
        set_guest_policy(self.c, self.o, night_charge=300, meal_charge=100); r = self.guest(eats_food=True); self.assertEqual(r["estimated_charge_paise"], 2 * 30000 + 2 * 10000)
        with self.assertRaises(RequestError): decide_guest(self.c, self.o, r["id"], False)                 # reject needs a reason
        decide_guest(self.c, self.o, r["id"], True, charge=800); self.assertEqual(tenant_ledger(self.c, self.o, self.t1)["pending_paise"], 80000)
        with self.assertRaises(RequestError): decide_guest(self.c, self.o, r["id"], True)
        r2 = self.guest(guest_name="Bina"); decide_guest(self.c, self.o, r2["id"], True); self.assertEqual(tenant_ledger(self.c, self.o, self.t1)["pending_paise"], 80000)   # no charge typed -> none added
        r3 = self.guest(guest_name="Cy"); decide_guest(self.c, self.o, r3["id"], False, "Exams this week"); self.assertEqual(my_guests(self.c, self.s1)[0]["status"], "rejected")

    def test_cancel_guest_and_charge_flag(self):
        r = self.guest(); decide_guest(self.c, self.o, r["id"], True, charge=500); cancel_guest(self.c, self.s1, r["id"], TODAY, now=NOW)
        self.assertIn("charge is still on", self.att()); self.assertEqual(tenant_ledger(self.c, self.o, self.t1)["pending_paise"], 50000)   # never auto-removed
        with self.assertRaises(AuthError): cancel_guest(self.c, self.s2, r["id"], TODAY)
        with self.assertRaises(RequestError): cancel_guest(self.c, self.s1, r["id"], TODAY)

    def test_pending_ending_and_overstay_on_dashboard(self):
        update_pg_details(self.c, self.o, guest_max_nights=1); p = self.guest(); self.assertIn("approval needed", self.att()); self.assertIn("limit 1", self.att())
        decide_guest(self.c, self.o, p["id"], True)
        self.assertEqual([x["kind"] for x in guest_reminders(self.c, self.o, "2030-03-22")], ["ends_today"]); self.assertEqual([x["kind"] for x in guest_reminders(self.c, self.o, "2030-03-23")], ["overstay"])
        now_late = datetime.fromisoformat("2030-03-23T12:00:00+05:30").timestamp()
        self.assertIn("was due to leave on 22 Mar", " | ".join(i["text"] for i in dashboard.today_dashboard(self.c, self.o, now_late)["needs_attention"]))
        mark_departed(self.c, self.s1, p["id"], now_late); self.assertEqual(guest_reminders(self.c, self.o, "2030-03-23"), [])

    def test_privacy_and_isolation(self):
        self.guest(); self.assertEqual(my_guests(self.c, self.s2), []); self.assertEqual(guest_rules(self.c, self.s1)["max_nights"], None)
        for f in (lambda: list_leaves(self.c, self.s1), lambda: decide_guest(self.c, self.s1, 1, True), lambda: decide_guest(self.c, self.o2, 1, True), lambda: set_guest_policy(self.c, self.s1, True), lambda: create_leave(self.c, self.o, TODAY)):
            with self.assertRaises(AuthError): f()
        self.assertEqual(list_leaves(self.c, self.o2, TODAY), []); self.assertEqual(guest_reminders(self.c, self.o2, TODAY), [])

if __name__ == "__main__": unittest.main()
