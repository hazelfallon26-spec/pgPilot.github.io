import unittest
from core import *
import core, setup
from setup import *

class T(unittest.TestCase):
    def setUp(self):
        self.c = setup.connect()
        self.pg1, _ = create_pg_with_owner(self.c, "Sunrise", "Asha", "a@x.com", "longpassword1")
        self.pg2, _ = create_pg_with_owner(self.c, "Other", "Bob", "b@x.com", "longpassword2")
        self.o1 = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1"))
        self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))

    def test_double_room_has_two_separate_beds_with_rent(self):
        set_floors(self.c, self.o1, 3); rid = add_room(self.c, self.o1, 2, "201", 2, 6500, 10000)
        beds = self.c.execute("SELECT label,rent_paise,deposit_paise FROM beds WHERE room_id=?", (rid,)).fetchall()
        self.assertEqual([(b[0], b[1], b[2]) for b in beds], [("A", 650000, 1000000), ("B", 650000, 1000000)])
        r2 = add_room(self.c, self.o1, 0, "101", 1, 7000, 10000)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM beds WHERE room_id=?", (r2,)).fetchone()[0], 1)

    def test_validation(self):
        set_floors(self.c, self.o1, 2); add_room(self.c, self.o1, 1, "101", 2, 5000, 5000)
        for args in [(1, "101", 2, 5000, 5000), (5, "501", 1, 1, 1), (1, "102", 1, -5, 0)]:
            with self.assertRaises(SetupError): add_room(self.c, self.o1, *args)
        with self.assertRaises(Exception): add_room(self.c, self.o1, 1, "103", 3, 5000, 5000)  # 3-sharing not allowed
        with self.assertRaises(SetupError): update_pg_details(self.c, self.o1, rent_due_day=31)
        with self.assertRaises(SetupError): update_pg_details(self.c, self.o1, late_fee_enabled=1)  # cannot be switched on here

    def test_same_room_number_allowed_in_other_pg(self):
        set_floors(self.c, self.o1, 1); set_floors(self.c, self.o2, 1)
        add_room(self.c, self.o1, 0, "101", 1, 5000, 5000); add_room(self.c, self.o2, 0, "101", 1, 9000, 9000)

    def test_isolation_and_roles(self):
        set_floors(self.c, self.o1, 1); rid = add_room(self.c, self.o1, 0, "101", 2, 5000, 5000)
        bed = self.c.execute("SELECT id FROM beds WHERE room_id=?", (rid,)).fetchone()[0]
        with self.assertRaises(AuthError): update_bed_rent(self.c, self.o2, bed, 1)   # other PG's owner
        t = get_session(self.c, accept_invite(self.c, create_invite(self.c, self.o1, "Ravi", "+91 98765-43210"), "4821"))
        with self.assertRaises(AuthError): add_room(self.c, t, 0, "999", 1, 1, 1)       # tenant blocked
        with self.assertRaises(AuthError): setup_status(self.c, t)
        get_session(self.c, login_tenant(self.c, "09876543210", "4821"))               # phone formats match

    def test_rent_change_is_audited_with_old_and_new(self):
        set_floors(self.c, self.o1, 1); rid = add_room(self.c, self.o1, 0, "101", 1, 5000, 5000)
        bed = self.c.execute("SELECT id FROM beds WHERE room_id=?", (rid,)).fetchone()[0]
        update_bed_rent(self.c, self.o1, bed, 5500)
        d = self.c.execute("SELECT detail FROM audit_log WHERE action='bed.rent_changed'").fetchone()[0]
        self.assertIn("[500000, 550000]", d)

    def test_staff_backup_and_checklist(self):
        self.assertFalse(setup_status(self.c, self.o1)["complete"])
        with self.assertRaises(SetupError): add_staff(self.c, self.o1, "wizard", "X", "9876543210")
        add_staff(self.c, self.o1, "cook", "Imran", "98765 43210"); set_backup_contact(self.c, self.o1, "Meena", "9123456789")
        set_floors(self.c, self.o1, 1); add_room(self.c, self.o1, 0, "101", 1, 5000, 5000)
        update_pg_details(self.c, self.o1, address="MG Road", phone="9876500000", rent_due_day=5)
        todo = setup_status(self.c, self.o1)["todo"]
        self.assertEqual(len(todo), 1); self.assertIn("Guest rule", todo[0])   # guest limit never invented
        update_pg_details(self.c, self.o1, guest_max_nights=2)
        self.assertTrue(setup_status(self.c, self.o1)["complete"])

if __name__ == "__main__": unittest.main()
