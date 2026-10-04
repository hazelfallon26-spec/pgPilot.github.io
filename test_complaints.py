import unittest
from core import *
import complaints
from complaints import *
from setup import set_floors, add_room, add_staff, set_backup_contact
from tenants import add_tenant, assign_bed

H = 3600; T0 = 1_900_000_000.0
AI = lambda cat, pr, s="x": (lambda text, cats: {"category": cat, "priority": pr, "summary": s})
def DOWN(text, cats): raise TimeoutError("AI unreachable")

class T(unittest.TestCase):
    def setUp(self):
        self.c = complaints.connect()
        create_pg_with_owner(self.c, "A", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 3); rid = add_room(self.c, self.o, 2, "204", 2, 6500, 10000)
        beds = [r[0] for r in self.c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        self.t1, self.s1 = self.tenant("Ravi", "9876543210", beds[0]); self.t2, self.s2 = self.tenant("Sam", "9123456789", beds[1])
        self.plumber = add_staff(self.c, self.o, "plumber", "Raj", "9000000001"); self.cook = add_staff(self.c, self.o, "cook", "Imran", "9000000002")
    def tenant(self, name, phone, bed):
        tid, inv = add_tenant(self.c, self.o, name, phone); s = get_session(self.c, accept_invite(self.c, inv, "4821")); assign_bed(self.c, self.o, tid, bed); return tid, s
    def row(self, cid): return self.c.execute("SELECT * FROM complaints WHERE id=?", (cid,)).fetchone()

    def test_classified_routed_with_deadline(self):
        cid = create_complaint(self.c, self.s1, "Room mein ceiling se paani tapak raha hai", AI("Plumbing", "HIGH", "Ceiling leak"), now=T0)
        r = self.row(cid); self.assertEqual((r["status"], r["priority"], r["assigned_staff_id"], r["room_number"], r["deadline"]), ("assigned", "HIGH", self.plumber, "204", T0 + 8 * H))
        self.assertEqual(my_complaints(self.c, self.s1)[0]["status"], "Being handled")

    def test_ai_down_or_missing_goes_to_needs_review_never_lost(self):
        a = create_complaint(self.c, self.s1, "Fan not working", DOWN, now=T0); b = create_complaint(self.c, self.s1, "Light not working", now=T0)
        for i, st in ((a, "unavailable"), (b, "not_connected")): r = self.row(i); self.assertEqual((r["status"], r["ai_state"], r["priority"]), ("needs_review", st, "MEDIUM"))
        junk = create_complaint(self.c, self.s1, "Tap broken", make_llm_classifier(lambda p: "not json"), now=T0); self.assertEqual(self.row(junk)["status"], "needs_review")
        bad = create_complaint(self.c, self.s1, "Tap leaking", make_llm_classifier(lambda p: '{"category":"Spaceships","priority":"LOW"}'), now=T0); self.assertEqual(self.row(bad)["status"], "needs_review")
        good = create_complaint(self.c, self.s1, "Food stale", make_llm_classifier(lambda p: '{"category":"Food","priority":"low","summary":"Stale"}'), now=T0); self.assertEqual(self.row(good)["assigned_staff_id"], self.cook)
        with self.assertRaises(ComplaintError): set_status(self.c, self.o, a, "in_progress")          # must be sorted first
        correct_category(self.c, self.o, a, "AC/Fan", now=T0); self.assertEqual(self.row(a)["status"], "new")   # no electrician configured -> unassigned, still visible
        self.assertEqual(complaint_counts(self.c, self.o, T0)["needs_review"], 3)

    def test_danger_keyword_forces_urgent_even_if_ai_says_low_or_is_down(self):
        a = create_complaint(self.c, self.s1, "Switch se sparks aa rahe hain", AI("Electricity", "LOW"), now=T0)
        b = create_complaint(self.c, self.s1, "gas leak in kitchen", DOWN, now=T0); c_ = create_complaint(self.c, self.s1, "Wifi firewall blocks my site", AI("Wi-Fi/Internet", "LOW"), now=T0)
        self.assertEqual((self.row(a)["priority"], self.row(b)["priority"], self.row(b)["status"], self.row(c_)["priority"]), ("URGENT", "URGENT", "needs_review", "LOW"))

    def test_duplicates_merge_and_both_tenants_confirm(self):
        a = create_complaint(self.c, self.s1, "Bathroom leaking", AI("Plumbing", "HIGH"), now=T0); b = create_complaint(self.c, self.s2, "Water leak in bathroom", AI("Plumbing", "HIGH"), now=T0 + 60)
        self.assertEqual(self.row(b)["possible_duplicate_of"], a); self.assertEqual(self.row(a)["possible_duplicate_of"], None)    # suggestion only, nothing auto-merged
        merge_complaints(self.c, self.o, b, a); self.assertEqual(len(list_complaints(self.c, self.o, T0)), 1)
        set_status(self.c, self.o, a, "resolved", "Pipe replaced", now=T0 + H)
        self.assertTrue(my_complaints(self.c, self.s2)[0]["needs_my_confirmation"])
        tenant_confirm(self.c, self.s2, a, False, now=T0 + 2 * H)                                       # the merged resident says NOT fixed -> reopens
        r = self.row(a); self.assertEqual((r["status"], r["reopen_count"], r["priority"]), ("new", 1, "HIGH")); self.assertEqual(complaint_counts(self.c, self.o, T0 + 2 * H)["reopened"], 1)

    def test_resolve_confirm_close_and_reopen_bumps_priority(self):
        a = create_complaint(self.c, self.s1, "Furniture broken", AI("Room/Furniture", "LOW"), now=T0)
        with self.assertRaises(ComplaintError): tenant_confirm(self.c, self.s1, a, True)                # not resolved yet
        set_status(self.c, self.o, a, "resolved", now=T0 + H)
        with self.assertRaises(AuthError): tenant_confirm(self.c, self.s2, a, True)                      # roommate never reported it
        tenant_confirm(self.c, self.s1, a, False, now=T0 + 2 * H); r = self.row(a)
        self.assertEqual((r["status"], r["priority"], r["deadline"]), ("new", "MEDIUM", T0 + 2 * H + 24 * H))
        set_status(self.c, self.o, a, "resolved", now=T0 + 3 * H); tenant_confirm(self.c, self.s1, a, True)
        self.assertEqual((self.row(a)["status"], my_complaints(self.c, self.s1)[0]["status"]), ("closed", "Confirmed"))
        with self.assertRaises(ComplaintError): set_status(self.c, self.o, a, "closed")                  # owner cannot self-close

    def test_staff_saying_done_is_not_enough_auto_close_is_recorded(self):
        a = create_complaint(self.c, self.s1, "Tap leaking", AI("Plumbing", "MEDIUM"), now=T0); set_status(self.c, self.o, a, "resolved", now=T0 + H)
        self.assertEqual(auto_close_unconfirmed(self.c, self.o, T0 + 50 * H), []); self.assertEqual(auto_close_unconfirmed(self.c, self.o, T0 + 80 * H), [a])
        self.assertIsNone(self.row(a)["tenant_confirmed"])

    def test_reminders_and_escalation_levels(self):
        a = create_complaint(self.c, self.s1, "Tap leaking", AI("Plumbing", "HIGH"), now=T0)                    # deadline T0+8h
        self.assertEqual(check_deadlines(self.c, self.o, T0 + 7 * H), [])
        self.assertEqual([(x["level"], x["to"]) for x in check_deadlines(self.c, self.o, T0 + 8 * H)], [(1, "staff")])
        self.assertEqual([(x["level"], x["to"]) for x in check_deadlines(self.c, self.o, T0 + 10 * H)], [(2, "owner")]); self.assertEqual(check_deadlines(self.c, self.o, T0 + 10 * H), [])
        self.assertEqual([(x["level"], x["to"]) for x in check_deadlines(self.c, self.o, T0 + 16 * H)], [(3, "backup")])   # owner never touched it
        b = create_complaint(self.c, self.s1, "Pipe burst", AI("Plumbing", "HIGH"), now=T0); set_status(self.c, self.o, b, "acknowledged", now=T0 + H)
        self.assertNotIn(3, [x["level"] for x in check_deadlines(self.c, self.o, T0 + 30 * H) if x["complaint_id"] == b])   # owner responded -> no backup
        u = create_complaint(self.c, self.s2, "Fire in switchboard", AI("Electricity", "HIGH"), now=T0 + 100 * H)
        self.assertEqual([x["to"] for x in check_deadlines(self.c, self.o, T0 + 100 * H + 31 * 60) if x["complaint_id"] == u], ["backup"])

    def test_retry_is_idempotent_and_validation(self):
        a = create_complaint(self.c, self.s1, "Tap leaking", idem_key="k", now=T0); self.assertEqual(a, create_complaint(self.c, self.s1, "Tap leaking", idem_key="k", now=T0))
        with self.assertRaises(ComplaintError): create_complaint(self.c, self.s1, "  ")

    def test_privacy_isolation_and_filters(self):
        a = create_complaint(self.c, self.s1, "Tap leaking", AI("Plumbing", "HIGH"), now=T0); create_complaint(self.c, self.s2, "Dinner bad", AI("Food", "LOW"), now=T0)
        self.assertEqual(len(my_complaints(self.c, self.s1)), 1); self.assertNotIn("Dinner", str(my_complaints(self.c, self.s1)))
        self.assertEqual([r["category"] for r in list_complaints(self.c, self.o, T0, floor=2, priority="HIGH")], ["Plumbing"])
        self.assertEqual(len(list_complaints(self.c, self.o, T0, room="204", tenant_id=self.t2)), 1)
        for f in (lambda: set_status(self.c, self.s1, a, "resolved"), lambda: list_complaints(self.c, self.s1), lambda: set_status(self.c, self.o2, a, "resolved"),
                  lambda: correct_category(self.c, self.o2, a, "Food"), lambda: assign_complaint(self.c, self.o2, a, self.plumber), lambda: create_complaint(self.c, self.o, "hi there")):
            with self.assertRaises(AuthError): f()
        self.assertEqual(list_complaints(self.c, self.o2, T0), [])
        assign_complaint(self.c, self.o, a, self.plumber, now=T0)

if __name__ == "__main__": unittest.main()
