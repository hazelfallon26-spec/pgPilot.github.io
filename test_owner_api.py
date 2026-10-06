import unittest, tempfile, os, json, threading
import server, core
from core import *
from test_server import Client
from tenants import room_overview

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = os.path.join(tempfile.mkdtemp(), "o.db"); server.init_db(cls.db); c = server.open_db(cls.db)
        create_pg_with_owner(c, "Sunrise PG", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(c, "Other PG", "Bob", "b@x.com", "longpassword2"); c.close()
        cls.srv = server.App(cls.db, 0); cls.port = cls.srv.server_address[1]; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()
    def owner(self, email="a@x.com", pw="longpassword1"):
        self.srv.tries = {}; c = Client(self.port); self.assertEqual(c.call("POST", "/api/login/owner", {"email": email, "password": pw})[0], 200); return c
    def ok(self, r): self.assertEqual(r[0], 200, r[1]); return r[1]

    def test_full_owner_journey(self):
        o = self.owner(); o2 = self.owner("b@x.com", "longpassword2")
        self.assertEqual(o.call("GET", "/api/me")[1]["pg"], "Sunrise PG"); self.assertFalse(self.ok(o.call("GET", "/api/owner/settings"))["setup"]["complete"])
        self.ok(o.call("POST", "/api/owner/settings", {"address": "MG Road", "phone": "9000000000", "rent_due_day": "5", "guest_max_nights": "2", "guest_rules": "Max 2 nights"}))
        self.ok(o.call("POST", "/api/owner/floors", {"count": 3})); self.ok(o.call("POST", "/api/owner/rooms", {"floor": 2, "room_number": "204", "capacity": "2", "rent": "6500", "deposit": "10000"}))
        self.ok(o.call("POST", "/api/owner/staff", {"role": "cook", "name": "Imran", "phone": "98765 00002", "language": "hi"})); self.ok(o.call("POST", "/api/owner/backup-contact", {"name": "Meena", "phone": "9123456780"}))
        self.assertEqual(self.ok(o.call("GET", "/api/owner/settings"))["setup"]["complete"], True)
        self.assertEqual(o.call("POST", "/api/owner/rooms", {"floor": 2, "room_number": "204", "capacity": "2", "rent": "1", "deposit": "1"})[0], 400)       # duplicate -> clear error, not a logout
        st, js, *_ = o.call("POST", "/api/owner/residents", {"name": "Ravi Kumar", "phone": "12345"}); self.assertEqual((st, js["error"]), (400, "Enter a valid 10-digit mobile number"))
        r = self.ok(o.call("POST", "/api/owner/residents", {"name": "Ravi Kumar", "phone": "+91 98765-43210"})); tid = r["id"]; tok = r["invite_path"].split("/")[-1]
        self.assertEqual(len(self.ok(o.call("GET", "/api/owner/residents"))["residents"]), 1)
        beds = self.ok(o.call("GET", "/api/owner/meta"))["vacant_beds"]; self.assertEqual(len(beds), 2)
        self.ok(o.call("POST", f"/api/owner/residents/{tid}/assign", {"bed_id": beds[0]["bed_id"], "move_in": "2030-03-01"}))
        self.assertEqual(o.call("POST", f"/api/owner/residents/{tid}/assign", {"bed_id": beds[1]["bed_id"]})[0], 400)
        ov = self.ok(o.call("GET", "/api/owner/rooms")); self.assertEqual((ov["occupied"], ov["vacant"], ov["rooms"][0]["beds"][1]["bed_id"] is not None), (1, 1, True))
        t = Client(self.port); self.ok(t.call("POST", "/api/join", {"token": tok, "pin": "4821"})); self.assertEqual(t.call("GET", "/api/tenant/home")[1]["room"], "204")           # invite link made by the owner screen really works
        self.assertEqual(o.call("POST", f"/api/owner/residents/{tid}/invite", {})[0], 400)                                                                               # already joined
        self.assertEqual(self.ok(o.call("POST", "/api/owner/charges/generate", {"period": "2030-03"}))["created"], 1); self.assertEqual(o.call("POST", "/api/owner/charges/generate", {"period": "bad"})[0], 400)
        p = self.ok(o.call("POST", "/api/owner/payments", {"tenant_id": tid, "amount": "4000", "method": "cash", "received_date": "2026-03-04", "idem_key": "z1"})); self.assertEqual(p["id"], self.ok(o.call("POST", "/api/owner/payments", {"tenant_id": tid, "amount": "4000", "method": "cash", "received_date": "2026-03-04", "idem_key": "z1"}))["id"])
        prof = self.ok(o.call("GET", f"/api/owner/residents/{tid}")); self.assertEqual((prof["pending"]["text"], prof["stay"]["rent"]["text"], prof["payments"][0]["can_reverse"]), ("₹2,500", "₹6,500", True))
        self.ok(o.call("POST", f"/api/owner/payments/{p['id']}/reverse", {"reason": "typo"})); prof = self.ok(o.call("GET", f"/api/owner/residents/{tid}")); self.assertEqual((prof["pending"]["text"], [x["can_reverse"] for x in prof["payments"]]), ("₹6,500", [False, False]))
        self.assertEqual(o.call("POST", f"/api/owner/payments/{p['id']}/reverse", {"reason": "again"})[0], 400); self.assertTrue(self.ok(o.call("GET", "/api/owner/payments"))["rows"])
        # complaints
        c1 = self.ok(t.call("POST", "/api/tenant/complaints", {"text": "Food complaint — Dinner: Too oily.", "hint": "Food"}))["id"]; c2 = self.ok(t.call("POST", "/api/tenant/complaints", {"text": "Sparks from socket"}))["id"]
        lst = self.ok(o.call("GET", "/api/owner/complaints?open=1"))["complaints"]; self.assertEqual({x["id"] for x in lst}, {c1, c2}); self.assertEqual(self.ok(o.call("GET", "/api/owner/complaints?status=needs_review"))["complaints"][0]["danger_word"], True)
        self.assertEqual(len(self.ok(o.call("GET", "/api/owner/complaints?category=Food"))["complaints"]), 1)
        self.assertEqual(o.call("POST", f"/api/owner/complaints/{c2}/status", {"status": "in_progress"})[0], 400)                                                         # must be sorted first
        self.ok(o.call("POST", f"/api/owner/complaints/{c2}/correct", {"category": "Electricity", "priority": "URGENT"})); staff = self.ok(o.call("GET", "/api/owner/meta"))["staff"][0]["id"]
        self.ok(o.call("POST", f"/api/owner/complaints/{c1}/assign", {"staff_id": staff})); self.ok(o.call("POST", f"/api/owner/complaints/{c1}/status", {"status": "resolved"}))
        self.assertTrue(t.call("GET", "/api/tenant/complaints")[1]["complaints"][1]["needs_my_confirmation"])
        d = self.ok(o.call("GET", "/api/owner/dashboard")); self.assertTrue(any(i["kind"] == "complaint" and "URGENT" == i["label"] for i in d["needs_attention"]))
        # guests, report, void, move-out
        g = self.ok(t.call("POST", "/api/tenant/guests", {"guest_name": "Rahul", "arrival": "2099-02-01", "departure": "2099-02-06", "overnight": True}))
        gl = self.ok(o.call("GET", "/api/owner/guests")); self.assertEqual((gl["guests"][0]["status"], gl["guests"][0]["reason"]), ("pending", "over_limit")); self.assertEqual(o.call("POST", f"/api/owner/guests/{g['id']}/decide", {"approve": False})[0], 400)
        self.ok(o.call("POST", f"/api/owner/guests/{g['id']}/decide", {"approve": True, "charge": "500"})); self.assertEqual(self.ok(o.call("GET", f"/api/owner/residents/{tid}"))["pending"]["text"], "₹7,000")
        ch = [x for x in self.ok(o.call("GET", f"/api/owner/residents/{tid}"))["charges"] if x["note"]][0]["id"]; self.ok(o.call("POST", f"/api/owner/charges/{ch}/void", {"reason": "waived"})); self.assertEqual(o.call("POST", f"/api/owner/charges/{ch}/void", {"reason": "x"})[0], 404)
        rp = self.ok(o.call("GET", "/api/owner/report/7")); self.assertEqual((rp["complaints_raised"], rp["occupied"]), (2, 1)); self.assertEqual(o.call("GET", "/api/owner/report/5")[0], 400)
        mo = self.ok(o.call("POST", f"/api/owner/residents/{tid}/moveout/prepare", {"date": "2030-03-31", "deductions": [{"label": "Damage", "amount": "1000"}]})); self.assertEqual((mo["dues"], mo["refund"]), ("₹6,500", "₹2,500"))
        self.assertEqual(o.call("POST", f"/api/owner/moveout/{mo['move_out_id']}/confirm", {"refund": "9999"})[0], 400); self.ok(o.call("POST", f"/api/owner/moveout/{mo['move_out_id']}/confirm", {"refund": mo["refund_number"]}))
        self.assertEqual(self.ok(o.call("GET", "/api/owner/rooms"))["occupied"], 0); self.assertEqual(t.call("GET", "/api/tenant/home")[0], 401)
        # isolation: the other PG's owner sees and touches nothing here
        self.assertEqual(self.ok(o2.call("GET", "/api/owner/residents"))["residents"], []); self.assertEqual(o2.call("GET", f"/api/owner/residents/{tid}")[0], 404); self.assertEqual(o2.call("POST", f"/api/owner/complaints/{c1}/status", {"status": "waiting"})[0], 404)
        self.assertEqual(o2.call("POST", "/api/owner/payments", {"tenant_id": tid, "amount": "1", "method": "cash"})[0], 404); self.assertEqual(self.ok(o2.call("GET", "/api/owner/complaints"))["complaints"], []); self.assertEqual(o2.call("POST", f"/api/owner/guests/{g['id']}/decide", {"approve": True})[0], 404)

    def test_late_fee_needs_confirmation_and_tenant_blocked(self):
        o = self.owner(); st, js, *_ = o.call("POST", "/api/owner/late-fee", {"amount": "200", "grace_days": 3}); self.assertEqual(st, 400); self.assertTrue(js["error"].startswith("Owner confirmation required"))
        self.ok(o.call("POST", "/api/owner/late-fee", {"amount": "200", "grace_days": 3, "confirm": True})); self.assertTrue(self.ok(o.call("GET", "/api/owner/settings"))["late_fee"]["enabled"]); self.ok(o.call("POST", "/api/owner/late-fee/disable", {}))
        self.assertEqual(o.call("POST", "/api/owner/settings", {"late_fee_enabled": 1})[0], 200)                      # unknown/forbidden field is ignored, never switched on
        self.assertFalse(self.ok(o.call("GET", "/api/owner/settings"))["late_fee"]["enabled"]); self.assertEqual(Client(self.port).call("GET", "/api/owner/settings")[0], 401)

if __name__ == "__main__": unittest.main()
