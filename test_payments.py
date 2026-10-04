import unittest, sqlite3
from core import *
import payments
from payments import *
from setup import set_floors, add_room, update_pg_details
from tenants import add_tenant, assign_bed, confirm_move_out

class T(unittest.TestCase):
    def setUp(self):
        self.c = payments.connect()
        create_pg_with_owner(self.c, "A", "Asha", "a@x.com", "longpassword1"); create_pg_with_owner(self.c, "B", "Bob", "b@x.com", "longpassword2")
        self.o = get_session(self.c, login_owner(self.c, "a@x.com", "longpassword1")); self.o2 = get_session(self.c, login_owner(self.c, "b@x.com", "longpassword2"))
        set_floors(self.c, self.o, 2); rid = add_room(self.c, self.o, 1, "101", 2, 6500, 10000)
        bed = self.c.execute("SELECT id FROM beds WHERE room_id=?", (rid,)).fetchone()[0]
        self.tid, inv = add_tenant(self.c, self.o, "Ravi", "9876543210"); self.ts = get_session(self.c, accept_invite(self.c, inv, "4821"))
        assign_bed(self.c, self.o, self.tid, bed, move_in="2030-10-01")
    def pend(self): return tenant_ledger(self.c, self.o, self.tid)["pending_paise"] / 100

    def test_due_day_required_and_idempotent(self):
        with self.assertRaises(TenantError): generate_rent_charges(self.c, self.o, "2030-10")
        update_pg_details(self.c, self.o, rent_due_day=5)
        self.assertEqual(generate_rent_charges(self.c, self.o, "2030-10"), 1); self.assertEqual(generate_rent_charges(self.c, self.o, "2030-10"), 0)

    def test_partial_then_full_and_tenant_view(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        record_payment(self.c, self.o, self.tid, 4000, "cash", today="2030-10-04"); self.assertEqual(self.pend(), 2500)
        v = my_payments(self.c, self.ts)["current"]; self.assertEqual((v["paid_paise"], v["remaining_paise"], v["due_date"]), (400000, 250000, "2030-10-05"))
        record_payment(self.c, self.o, self.tid, 2500, "upi", today="2030-10-04"); self.assertEqual(self.pend(), 0)

    def test_advance_covers_next_month(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        record_payment(self.c, self.o, self.tid, 13000, "bank", today="2030-10-02")
        self.assertEqual(tenant_ledger(self.c, self.o, self.tid)["advance_credit_paise"], 650000)
        generate_rent_charges(self.c, self.o, "2030-11"); self.assertEqual(self.pend(), 0)

    def test_reversal_not_edit(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        p = record_payment(self.c, self.o, self.tid, 65000, "cash", today="2030-10-04")   # typo: extra zero
        with self.assertRaises(TenantError): reverse_payment(self.c, self.o, p, "  ")
        r = reverse_payment(self.c, self.o, p, "Typed 65000 instead of 6500"); self.assertEqual(self.pend(), 6500)
        with self.assertRaises(TenantError): reverse_payment(self.c, self.o, p, "again")
        with self.assertRaises(TenantError): reverse_payment(self.c, self.o, r, "undo")
        self.assertEqual(len(tenant_ledger(self.c, self.o, self.tid)["payments"]), 2)       # both rows still visible
        for sql in ("DELETE FROM payments", "UPDATE payments SET amount_paise=1"):
            with self.assertRaises(sqlite3.DatabaseError): self.c.execute(sql)

    def test_retry_does_not_double_record_and_validation(self):
        a = record_payment(self.c, self.o, self.tid, 1000, "cash", idem_key="k1", today="2030-10-04")
        self.assertEqual(a, record_payment(self.c, self.o, self.tid, 1000, "cash", idem_key="k1", today="2030-10-04"))
        for bad in (dict(amount=0), dict(amount=-5), dict(amount=10, method="bitcoin"), dict(amount=10, received_date="2030-12-01")):
            kw = dict(amount=10, method="cash", today="2030-10-04"); kw.update(bad)
            with self.assertRaises(TenantError): record_payment(self.c, self.o, self.tid, **kw)

    def test_late_fee_off_by_default_needs_confirm_and_waiver_sticks(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        self.assertEqual(apply_late_fees(self.c, self.o, "2030-12-01"), []); self.assertEqual(self.pend(), 6500)
        with self.assertRaises(TenantError): configure_late_fee(self.c, self.o, 200, 3)
        self.assertEqual(apply_late_fees(self.c, self.o, "2030-12-01"), [])
        configure_late_fee(self.c, self.o, 200, 3, confirm=True)
        self.assertEqual(apply_late_fees(self.c, self.o, "2030-10-07"), [])                  # still inside grace
        self.assertEqual(len(apply_late_fees(self.c, self.o, "2030-10-09")), 1); self.assertEqual(apply_late_fees(self.c, self.o, "2030-10-20"), [])
        self.assertEqual(self.pend(), 6700)
        fee = [x for x in tenant_ledger(self.c, self.o, self.tid)["charges"] if x["kind"] == "late_fee"][0]["id"]
        void_charge(self.c, self.o, fee, "Waived - first time"); apply_late_fees(self.c, self.o, "2030-10-25"); self.assertEqual(self.pend(), 6500)

    def test_deposit_status(self):
        record_payment(self.c, self.o, self.tid, 4000, "cash", purpose="deposit", today="2030-10-04")
        self.assertEqual(tenant_ledger(self.c, self.o, self.tid)["deposit"]["status"], "partial")
        with self.assertRaises(TenantError): record_payment(self.c, self.o, self.tid, 7000, "cash", purpose="deposit", today="2030-10-04")
        record_payment(self.c, self.o, self.tid, 6000, "cash", purpose="deposit", today="2030-10-04")
        self.assertEqual(my_payments(self.c, self.ts)["deposit"]["status"], "paid")

    def test_isolation_and_dashboard_feed(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        s = pending_summary(self.c, self.o, "2030-10-10"); self.assertEqual((s["total_overdue_paise"], s["rows"][0]["status"]), (650000, "overdue"))
        for f in (lambda: record_payment(self.c, self.o2, self.tid, 10, "cash"), lambda: tenant_ledger(self.c, self.o2, self.tid), lambda: record_payment(self.c, self.ts, self.tid, 10, "cash"),
                  lambda: reverse_payment(self.c, self.o2, 1, "x"), lambda: my_payments(self.c, self.o)):
            with self.assertRaises(AuthError): f()
        self.assertEqual(pending_summary(self.c, self.o2)["rows"], [])

    def test_move_out_prefill_from_ledger(self):
        update_pg_details(self.c, self.o, rent_due_day=5); generate_rent_charges(self.c, self.o, "2030-10")
        record_payment(self.c, self.o, self.tid, 4000, "cash", today="2030-10-04")
        p = prepare_move_out_prefilled(self.c, self.o, self.tid, "2030-10-31"); self.assertEqual((p["dues"], p["refund"]), (2500, 7500))

if __name__ == "__main__": unittest.main()
