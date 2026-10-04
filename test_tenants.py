import unittest
from core import *
import tenants
from tenants import *
from setup import set_floors, add_room, update_bed_rent

class T(unittest.TestCase):
    def setUp(self):
        self.c = tenants.connect()
        create_pg_with_owner(self.c, "Sunrise", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "Other", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 3); self.r201 = add_room(self.c, self.o, 2, "201", 2, 6500, 10000); self.r202 = add_room(self.c, self.o, 2, "202", 2, 7000, 10000)
        q = lambda r, l: self.c.execute("SELECT id FROM beds WHERE room_id=? AND label=?", (r, l)).fetchone()[0]
        self.a201, self.b201, self.a202 = q(self.r201, "A"), q(self.r201, "B"), q(self.r202, "A")
    def tenant(self, name="Ravi", phone="9876543210", bed=None):
        tid, inv = add_tenant(self.c, self.o, name, phone)
        s = get_session(self.c, accept_invite(self.c, inv, "4821"))
        if bed: assign_bed(self.c, self.o, tid, bed)
        return tid, s

    def test_half_occupied_double_room(self):
        self.tenant(bed=self.a201); ov = room_overview(self.c, self.o); r = [x for x in ov["rooms"] if x["room"] == "201"][0]
        self.assertEqual((r["occupied"], r["capacity"]), (1, 2)); self.assertIsNone(r["beds"][1]["occupant"])
        self.assertEqual((ov["total_beds"], ov["occupied"], ov["vacant"]), (4, 1, 3))

    def test_no_double_booking_and_dup_phone(self):
        t1, _ = self.tenant(bed=self.a201); t2, _ = self.tenant("Sam", "9123456789")
        with self.assertRaises(TenantError): assign_bed(self.c, self.o, t2, self.a201)
        with self.assertRaises(TenantError): assign_bed(self.c, self.o, t1, self.b201)
        with self.assertRaises(TenantError): add_tenant(self.c, self.o, "Dup", "+91 98765 43210")

    def test_move_keeps_history_and_rent(self):
        t, _ = self.tenant(bed=self.a201); res = move_tenant(self.c, self.o, t, self.a202)
        self.assertFalse(res["rent_changed"]); self.assertIn("7000", res["warning"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM stays WHERE tenant_id=?", (t,)).fetchone()[0], 2)
        self.assertEqual(my := room_overview(self.c, self.o)["occupied"], 1)
        self.assertEqual(self.c.execute("SELECT rent_paise FROM stays WHERE end_date IS NULL").fetchone()[0], 650000)

    def test_price_changes_never_silent(self):
        t, _ = self.tenant(bed=self.a201); update_bed_rent(self.c, self.o, self.a201, 9000)
        self.assertEqual(self.c.execute("SELECT rent_paise FROM stays WHERE tenant_id=?", (t,)).fetchone()[0], 650000)
        with self.assertRaises(TenantError): change_tenant_rent(self.c, self.o, t, 9000)
        change_tenant_rent(self.c, self.o, t, 9000, confirm=True)

    def test_move_out_requires_confirmed_figures(self):
        t, s = self.tenant(bed=self.a201)
        p = prepare_move_out(self.c, self.o, t, "2030-01-31", dues=2500, deductions=[("Damage", 1000)])
        self.assertEqual(p["refund"], 6500)
        with self.assertRaises(TenantError): confirm_move_out(self.c, self.o, p["move_out_id"], 10000)
        self.assertEqual(room_overview(self.c, self.o)["occupied"], 1)       # nothing changed yet
        confirm_move_out(self.c, self.o, p["move_out_id"], 6500)
        self.assertEqual(room_overview(self.c, self.o)["occupied"], 0)       # bed freed
        with self.assertRaises(AuthError): get_session(self.c, login_tenant(self.c, "9876543210", "4821"))
        with self.assertRaises(AuthError): login_tenant(self.c, "9876543210", "4821")

    def test_tenant_owes_when_dues_exceed_deposit(self):
        t, _ = self.tenant(bed=self.a201); p = prepare_move_out(self.c, self.o, t, "2030-01-31", dues=13000)
        self.assertEqual((p["refund"], p["tenant_owes"]), (-3000, 3000))

    def test_privacy_and_isolation(self):
        t1, s1 = self.tenant(bed=self.a201); self.tenant("Sam", "9123456789", self.b201)
        p = my_profile(self.c, s1); self.assertEqual((p["name"], p["room"], p["bed"]), ("Ravi", "201", "A")); self.assertNotIn("Sam", str(p))
        for f in (lambda: assign_bed(self.c, s1, t1, self.a202), lambda: room_overview(self.c, s1), lambda: add_tenant(self.c, s1, "X", "9000000000")): 
            with self.assertRaises(AuthError): f()
        with self.assertRaises(AuthError): assign_bed(self.c, self.o2, t1, self.a202)      # other PG's owner
        with self.assertRaises(AuthError): prepare_move_out(self.c, self.o2, t1, "2030-01-01")
        with self.assertRaises(AuthError): my_profile(self.c, self.o)

if __name__ == "__main__": unittest.main()
