import unittest, tempfile, os, json, threading, http.client, re
import server, core
from core import *
from setup import set_floors, add_room, update_pg_details, add_staff
from tenants import add_tenant, assign_bed
from payments import generate_rent_charges
from complaints import set_status

class Client:
    def __init__(s, port): s.port, s.cookie = port, None
    def call(s, method, path, body=None, csrf=True, raw=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if csrf: h["X-PG"] = "1"
        if s.cookie: h["Cookie"] = s.cookie
        conn = http.client.HTTPConnection("127.0.0.1", s.port); conn.request(method, path, raw if raw is not None else (json.dumps(body) if body is not None else None), h); r = conn.getresponse(); data = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and "sid=;" not in sc: s.cookie = sc.split(";")[0]
        if sc and "sid=;" in sc: s.cookie = None
        try: js = json.loads(data)
        except Exception: js = None
        return r.status, js, r, data

import itertools
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(); cls.db = os.path.join(cls.dir, "t.db"); server.init_db(cls.db); c = server.open_db(cls.db)
        pg, _ = create_pg_with_owner(c, "Sunrise PG", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(c, "Other PG", "Bob", "b@x.com", "longpassword2")
        o = get_session(c, login_owner(c, "a@x.com", "longpassword1")); set_floors(c, o, 3); cls.beds = []
        for n in range(204, 214):
            rid = add_room(c, o, 2, str(n), 2, 6500, 10000); cls.beds += [r[0] for r in c.execute("SELECT id FROM beds WHERE room_id=? ORDER BY label", (rid,))]
        update_pg_details(c, o, rent_due_day=5, address="x", phone="9000000000", guest_max_nights=2, guest_rules="Max 2 nights."); add_staff(c, o, "cook", "Imran", "9000000002"); c.close()
        cls.n = itertools.count(100000); cls.bi = iter(cls.beds)
        cls.srv = server.App(cls.db, 0); cls.port = cls.srv.server_address[1]; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()
    def mk(self, name="Test Tenant", bed=True):
        """A fresh tenant with a bed and March rent charged -> (tenant_id, invite_token)."""
        db = server.open_db(self.db); o = get_session(db, login_owner(db, "a@x.com", "longpassword1")); tid, inv = add_tenant(db, o, name, f"9000{next(self.n):06d}")
        if bed: assign_bed(db, o, tid, next(self.bi), move_in="2030-03-01"); generate_rent_charges(db, o, "2030-03")
        db.close(); return tid, inv
    def login(self, inv, pin="4821"):
        c = Client(self.port); st, js, r, _ = c.call("POST", "/api/join", {"token": inv, "pin": pin}); self.assertEqual(st, 200); return c, r

    def test_static_security_headers_and_cookie_flags(self):
        c = Client(self.port); st, _, r, data = c.call("GET", "/"); self.assertEqual(st, 200); self.assertIn(b"My PG", data); self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy")); self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(c.call("GET", "/join/abc")[0], 200); self.assertEqual(c.call("GET", "/static/app.js")[0], 200); self.assertEqual(c.call("GET", "/static/../server.py")[0], 404); self.assertEqual(c.call("GET", "/static/server.py")[0], 404)
        self.assertNotIn(b"onclick", c.call("GET", "/static/app.js")[3])                                                                        # nothing the CSP would block
        _, inv = self.mk(); _, r2 = self.login(inv); sc = r2.getheader("Set-Cookie"); self.assertIn("HttpOnly", sc); self.assertIn("SameSite=Strict", sc)

    def test_tenant_journey(self):
        tid, inv = self.mk("Ravi Kumar"); info = Client(self.port).call("GET", f"/api/invite/{inv}"); self.assertEqual((info[1]["name"], info[1]["pg"]), ("Ravi Kumar", "Sunrise PG"))
        c, _ = self.login(inv); self.assertEqual(c.call("POST", "/api/join", {"token": inv, "pin": "4821"})[0], 401); self.assertEqual(Client(self.port).call("GET", f"/api/invite/{inv}")[0], 401)   # single use
        h = c.call("GET", "/api/tenant/home")[1]; self.assertEqual((h["name"], h["rent"]["amount"]["text"], h["rent"]["remaining"]["text"], h["rent"]["due_text"]), ("Ravi Kumar", "₹6,500", "₹6,500", "5 Mar")); self.assertTrue(h["room"])
        r = c.call("GET", "/api/tenant/rent")[1]; self.assertEqual((r["deposit"]["status"], r["total_pending"]["text"]), ("not paid", "₹6,500"))
        st, js, *_ = c.call("POST", "/api/tenant/complaints", {"text": "Food complaint — Dinner: Too oily.", "hint": "Food", "idem_key": "k1"}); self.assertEqual((st, js["status"]), (200, "Being handled"))      # no AI: tile category used, routed to the cook
        self.assertEqual(c.call("POST", "/api/tenant/complaints", {"text": "Food complaint — Dinner: Too oily.", "hint": "Food", "idem_key": "k1"})[1]["id"], js["id"])
        row = server.open_db(self.db).execute("SELECT ai_state, assigned_staff_id FROM complaints WHERE id=?", (js["id"],)).fetchone(); self.assertEqual(row["ai_state"], "tenant_selected"); self.assertIsNotNone(row["assigned_staff_id"])
        j2 = c.call("POST", "/api/tenant/complaints", {"text": "Switch me sparks aa rahe hain"})[1]; self.assertEqual(j2["status"], "Reported")                                                              # AI absent -> Needs review, still saved
        self.assertEqual(tuple(server.open_db(self.db).execute("SELECT priority,status FROM complaints WHERE id=?", (j2["id"],)).fetchone()), ("URGENT", "needs_review"))
        self.assertEqual(len(c.call("GET", "/api/tenant/complaints")[1]["complaints"]), 2)
        self.assertEqual(c.call("POST", "/api/tenant/assistant", {"text": "When is my rent due?"})[1]["answer"], "Your rent of ₹6,500 is due on 5 Mar.")
        self.assertEqual(c.call("POST", "/api/tenant/leaves", {"leave_date": "2099-01-10", "return_date": "2099-01-15", "reason": "Home"})[0], 200); self.assertEqual(c.call("GET", "/api/tenant/leaves")[1]["leaves"][0]["status"], "active")
        g = c.call("POST", "/api/tenant/guests", {"guest_name": "Rahul", "arrival": "2099-02-01", "departure": "2099-02-03", "overnight": True, "eats_food": False})[1]; self.assertEqual(g["status"], "approved")
        self.assertEqual(c.call("GET", "/api/tenant/guests")[1]["rules"]["max_nights"], 2); self.assertEqual(c.call("POST", f"/api/tenant/guests/{g['id']}/cancel", {})[0], 200)
        self.assertEqual(c.call("POST", "/api/tenant/notifications/read", {})[0], 200); self.assertEqual(c.call("POST", "/api/logout", {})[0], 200); self.assertEqual(c.call("GET", "/api/tenant/home")[0], 401)

    def test_confirm_flow_and_privacy(self):
        _, inv = self.mk("Ravi Kumar"); _, inv2 = self.mk("Sam Other"); c, _ = self.login(inv); c2, _ = self.login(inv2)
        st, js, *_ = c.call("POST", "/api/tenant/complaints", {"text": "Tap leaking badly", "hint": "Plumbing"}); cid = js["id"]
        db = server.open_db(self.db); o = get_session(db, login_owner(db, "a@x.com", "longpassword1")); set_status(db, o, cid, "resolved")
        self.assertTrue(c.call("GET", "/api/tenant/complaints")[1]["complaints"][0]["needs_my_confirmation"]); self.assertEqual(c.call("GET", "/api/tenant/home")[1]["needs_confirmation"], 1)
        self.assertEqual(c2.call("POST", f"/api/tenant/complaints/{cid}/confirm", {"fixed": True})[0], 404)          # another resident cannot answer for you
        self.assertEqual(c.call("POST", f"/api/tenant/complaints/{cid}/confirm", {"fixed": False})[0], 200); self.assertNotIn("leaking", json.dumps(c2.call("GET", "/api/tenant/complaints")[1]))

    def test_auth_csrf_roles_and_limits(self):
        self.srv.tries = {}; c = Client(self.port); self.assertEqual(c.call("GET", "/api/tenant/home")[0], 401)
        st, js, *_ = c.call("POST", "/api/login/tenant", {"phone": "9876543210", "pin": "0000"}); self.assertEqual((st, js["error"]), (401, "Wrong details, or account temporarily locked"))
        self.assertEqual(c.call("POST", "/api/login/owner", {"email": "a@x.com", "password": "longpassword1"})[0], 200); self.assertEqual(c.call("GET", "/api/me")[1]["role"], "owner")
        d = c.call("GET", "/api/owner/dashboard"); self.assertEqual(d[0], 200); self.assertIn("glance", d[1]); self.assertEqual(c.call("GET", "/api/tenant/home")[0], 403)                          # owner cannot use tenant endpoints
        self.assertEqual(c.call("POST", "/api/owner/notices", {"title": "Water off", "body": "Tuesday 10-12", "pinned": True})[0], 200); self.assertEqual(c.call("GET", "/api/notices")[1]["notices"][0]["title"], "Water off")
        self.assertEqual(c.call("POST", "/api/owner/notices", {"title": "", "body": "x"})[0], 400)
        self.assertEqual(c.call("POST", "/api/owner/notices", {"title": "x", "body": "y"}, csrf=False)[0], 403)                                                                                         # missing CSRF header
        self.assertEqual(c.call("POST", "/api/owner/notices", raw="{not json")[0], 400); self.assertEqual(c.call("POST", "/api/owner/notices", raw="x" * 30000)[0], 413); self.assertEqual(c.call("GET", "/api/nope")[0], 404)

    def test_tenant_blocked_from_owner_api_unassigned_tenant_and_login_throttle(self):
        _, inv = self.mk("Zed", bed=False); c, _ = self.login(inv); self.assertEqual(c.call("GET", "/api/owner/dashboard")[0], 403); self.assertEqual(c.call("POST", "/api/owner/notices", {"title": "a", "body": "b"})[0], 403)
        st, js, *_ = c.call("GET", "/api/tenant/home"); self.assertIn(st, (200, 400, 404)); self.assertNotEqual(st, 500)                                                                                    # no bed yet: handled, never a crash
        self.srv.tries = {}; codes = [Client(self.port).call("POST", "/api/login/tenant", {"phone": "9000000000", "pin": "1"})[0] for _ in range(32)]; self.assertEqual(codes[-1], 429); self.srv.tries = {}

if __name__ == "__main__": unittest.main()
