import unittest, tempfile, os, sqlite3
from datetime import datetime
from core import *
import notifications, dashboard
from notifications import *
from complaints import create_complaint, set_status, merge_complaints
from leave_guests import create_guest_request, decide_guest
from setup import set_floors, add_room, add_staff, update_pg_details, set_backup_contact
from tenants import add_tenant, assign_bed, prepare_move_out, confirm_move_out
from payments import generate_rent_charges, record_payment

H = 3600; T0 = datetime.fromisoformat("2030-03-17T12:00:00+05:30").timestamp(); TODAY = "2030-03-17"
AI = lambda cat, pr, s="Tap leak": (lambda text, cats: {"category": cat, "priority": pr, "summary": s})

class T(unittest.TestCase):
    def setUp(self):
        self.saved = dict(notifications.PROVIDERS)
        self.c = notifications.connect()
        self.pg, _ = create_pg_with_owner(self.c, "Sunrise PG", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 3); rid = add_room(self.c, self.o, 2, "204", 2, 6500, 10000); beds = [r[0] for r in self.c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        self.t1, self.s1 = self.tenant("Ravi", "9876543210", beds[0]); self.t2, self.s2 = self.tenant("Sam", "9123456789", beds[1])
        self.plumber = add_staff(self.c, self.o, "plumber", "Raj", "9000000001", "hi"); update_pg_details(self.c, self.o, rent_due_day=5, guest_max_nights=2, address="x", phone="9000000000")
    def tearDown(self): notifications.PROVIDERS.clear(); notifications.PROVIDERS.update(self.saved)
    def tenant(self, n, ph, bed):
        tid, inv = add_tenant(self.c, self.o, n, ph); s = get_session(self.c, accept_invite(self.c, inv, "4821")); assign_bed(self.c, self.o, tid, bed, move_in="2030-03-01"); return tid, s
    def row(self, nid): return self.c.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone()

    def test_not_connected_is_never_pretended_delivered(self):
        n = notify(self.c, self.pg, "staff", self.plumber, "x", "t", "k1", T0); r = self.row(n)
        self.assertEqual((r["status"], r["last_error"]), ("not_delivered", "SMS not connected")); rep = delivery_report(self.c, self.o, True)[0]
        self.assertEqual((rep["status"], rep["attempts"][0]["ok"]), ("Not delivered", 0)); self.assertIn("could not be delivered", " | ".join(i["text"] for i in dashboard.today_dashboard(self.c, self.o, T0)["needs_attention"]))
        t = notify(self.c, self.pg, "tenant", self.t1, "hello", "t", "k2", T0); self.assertEqual(self.row(t)["status"], "delivered"); self.assertEqual(my_notifications(self.c, self.s1)[0]["message"], "hello")

    def test_sent_vs_delivered_retry_and_attempt_log(self):
        register_provider("sms", lambda to, m: (True, "accepted", False)); self.assertEqual(self.row(notify(self.c, self.pg, "staff", self.plumber, "x", "t", "a", T0))["status"], "sent")   # accepted != confirmed delivered
        calls = []
        def flaky(to, m): calls.append(1); return (False, "timeout", False)
        register_provider("sms", flaky); n = notify(self.c, self.pg, "staff", self.plumber, "x", "t", "b", T0)
        self.assertEqual((self.row(n)["status"], self.row(n)["attempts"]), ("queued", 1)); self.assertEqual(retry_due(self.c, self.pg, T0 + 60), 0)    # backoff respected
        self.assertEqual(retry_due(self.c, self.pg, T0 + 301), 1); self.assertEqual(retry_due(self.c, self.pg, T0 + 301 + 1801), 1)
        self.assertEqual((self.row(n)["status"], len(calls)), ("not_delivered", 3)); self.assertEqual(len(delivery_report(self.c, self.o)[0]["attempts"]), 3)
        with self.assertRaises(sqlite3.DatabaseError): self.c.execute("UPDATE notification_attempts SET ok=1")
        register_provider("sms", lambda to, m: 1 / 0); self.assertEqual(self.row(notify(self.c, self.pg, "staff", self.plumber, "x", "t", "c", T0))["status"], "queued")   # provider crash is caught, retried

    def test_complaint_escalation_messages_privacy_and_dedupe(self):
        got = []; register_provider("sms", lambda to, m: (got.append((to, m)), (True, "ok", False))[1])
        set_backup_contact(self.c, self.o, "Meena", "9123456780")
        cid = create_complaint(self.c, self.s1, "Ceiling leak, Ravi here", AI("Plumbing", "HIGH"), now=T0)
        run_scheduler(self.c, self.pg, T0 + 8 * H); r = run_scheduler(self.c, self.pg, T0 + 16 * H); self.assertEqual(run_scheduler(self.c, self.pg, T0 + 16 * H)["new_notifications"], 0)   # run twice: no duplicates
        by = {to: m for to, m in got}; self.assertIn("Namaste", by["9000000001"]); self.assertIn("Room 204", by["9000000001"]); self.assertNotIn("Ravi", " ".join(by.values())); self.assertNotIn("9876543210", " ".join(by.values()))
        self.assertIn("owner has not responded", by["9123456780"]); self.assertTrue(any("overdue" in m["message"] for m in owner_inbox(self.c, self.o)))

    def test_no_backup_contact_means_not_delivered_not_silence(self):
        register_provider("sms", lambda to, m: (True, "ok", False)); create_complaint(self.c, self.s1, "Tap leak", AI("Plumbing", "HIGH"), now=T0); run_scheduler(self.c, self.pg, T0 + 16 * H)
        self.assertIn("No phone number on file", [r["reason"] for r in delivery_report(self.c, self.o, True)])

    def test_rent_reminders(self):
        generate_rent_charges(self.c, self.o, "2030-03"); generate_rent_charges(self.c, self.o, "2030-04")
        run_scheduler(self.c, self.pg, datetime.fromisoformat("2030-03-03T10:00:00+05:30").timestamp()); self.assertIn("Rent reminder: ₹6,500 is due on 5 Mar", my_notifications(self.c, self.s1)[0]["message"])
        n = len(my_notifications(self.c, self.s1)); run_scheduler(self.c, self.pg, datetime.fromisoformat("2030-03-03T18:00:00+05:30").timestamp()); self.assertEqual(len(my_notifications(self.c, self.s1)), n)
        for d in ("2030-03-06", "2030-03-09", "2030-03-14"): run_scheduler(self.c, self.pg, datetime.fromisoformat(d + "T10:00:00+05:30").timestamp())
        self.assertEqual(sum("overdue" in m["message"] for m in my_notifications(self.c, self.s1)), 2)           # day after due + weekly
        record_payment(self.c, self.o, self.t2, 13000, "cash", today="2030-03-14"); self.assertEqual(my_notifications(self.c, self.s2), [])           # advance payer is never nagged... until nothing owed
        self.assertTrue(any("overdue" in m["message"] for m in my_notifications(self.c, self.s2)) is False)

    def test_resolved_asks_every_reporter_to_confirm(self):
        a = create_complaint(self.c, self.s1, "Leak", AI("Plumbing", "HIGH"), now=T0); b = create_complaint(self.c, self.s2, "Water leak", AI("Plumbing", "HIGH"), now=T0 + 60)
        merge_complaints(self.c, self.o, b, a); set_status(self.c, self.o, a, "resolved", now=T0 + H); run_scheduler(self.c, self.pg, T0 + 2 * H)
        for s in (self.s1, self.s2): self.assertTrue(any("Is the problem fixed" in m["message"] for m in my_notifications(self.c, s)))

    def test_guest_reminders_and_isolation(self):
        g = create_guest_request(self.c, self.s1, "Rahul", "2030-03-17", "2030-03-18", True, today=TODAY, now=T0); run_scheduler(self.c, self.pg, datetime.fromisoformat("2030-03-18T09:00:00+05:30").timestamp())
        self.assertTrue(any("due to leave today" in m["message"] for m in my_notifications(self.c, self.s1))); self.assertTrue(any("due to leave" in m["message"] for m in owner_inbox(self.c, self.o)))
        self.assertEqual(my_notifications(self.c, self.s2), []); self.assertEqual(owner_inbox(self.c, self.o2), [])
        for f in (lambda: delivery_report(self.c, self.s1), lambda: owner_inbox(self.c, self.s1), lambda: my_notifications(self.c, self.o)):
            with self.assertRaises(AuthError): f()

    def test_backup_valid_rotates_and_restores(self):
        d = tempfile.mkdtemp(); p = daily_backup_if_due(self.c, d, T0); self.assertTrue(verify_backup(p)); self.assertIsNone(daily_backup_if_due(self.c, d, T0 + H)); self.assertEqual(oct(os.stat(p).st_mode)[-3:], "600")
        self.assertEqual(sqlite3.connect(p).execute("SELECT COUNT(*) FROM tenants").fetchone()[0], 2)
        for i in range(1, 20): backup_database(self.c, d, T0 + i * 86400)
        self.assertEqual(len(os.listdir(d)), 14); open(os.path.join(d, "bad.db"), "w").write("junk"); self.assertFalse(verify_backup(os.path.join(d, "bad.db")))

    def test_data_deletion_flow(self):
        generate_rent_charges(self.c, self.o, "2030-03"); record_payment(self.c, self.o, self.t1, 6500, "cash", today="2030-03-04"); inv_old = None
        create_complaint(self.c, self.s1, "Ravi's fan broken", AI("AC/Fan", "LOW"), now=T0); rid = request_deletion(self.c, self.s1, T0)
        self.assertIn("asked for their personal data", " | ".join(i["text"] for i in dashboard.today_dashboard(self.c, self.o, T0)["needs_attention"]))
        with self.assertRaises(TenantError): process_deletion(self.c, self.o, rid, True)                        # still living there
        p = prepare_move_out(self.c, self.o, self.t1, "2030-03-31"); confirm_move_out(self.c, self.o, p["move_out_id"], p["refund"])
        with self.assertRaises(TenantError): process_deletion(self.c, self.o, rid)                               # needs explicit owner confirmation
        with self.assertRaises(AuthError): process_deletion(self.c, self.o2, rid, True)
        process_deletion(self.c, self.o, rid, True, T0)
        t = self.c.execute("SELECT * FROM tenants WHERE id=?", (self.t1,)).fetchone(); self.assertEqual((t["name"], t["phone"], t["email"]), (f"Deleted resident {self.t1}", f"deleted-{self.t1}", None))
        self.assertEqual(self.c.execute("SELECT description FROM complaints WHERE tenant_id=?", (self.t1,)).fetchone()[0], "[removed at resident request]")
        self.assertEqual(self.c.execute("SELECT SUM(amount_paise) FROM payments WHERE tenant_id=?", (self.t1,)).fetchone()[0], 650000)                # money kept
        dump = " ".join(str(tuple(r)) for r in self.c.execute("SELECT * FROM audit_log")); self.assertNotIn("Ravi", dump); self.assertNotIn("9876543210", dump)
        self.assertGreater(self.c.execute("SELECT COUNT(*) FROM audit_log WHERE action='tenant.added'").fetchone()[0], 0)                              # rows stay, names don't
        with self.assertRaises(sqlite3.DatabaseError): self.c.execute("UPDATE audit_log SET detail='tamper'")                                         # gate is closed again
        with self.assertRaises(AuthError): login_tenant(self.c, "9876543210", "4821")
        add_tenant(self.c, self.o, "Ravi Again", "9876543210")                                                                                         # number can be re-invited

if __name__ == "__main__": unittest.main()
