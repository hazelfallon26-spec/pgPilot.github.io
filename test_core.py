import unittest, sqlite3, time
from core import *

class T(unittest.TestCase):
    def setUp(self):
        self.c = connect()
        self.pg1, self.o1 = create_pg_with_owner(self.c, "Sunrise PG", "Asha", "a@x.com", "longpassword1")
        self.pg2, self.o2 = create_pg_with_owner(self.c, "Other PG", "Bob", "b@x.com", "longpassword2")
        self.owner = get_session(self.c, login_owner(self.c, "A@x.com", "longpassword1"))

    def test_owner_wrong_password_and_lockout(self):
        for _ in range(5):
            with self.assertRaises(AuthError): login_owner(self.c, "a@x.com", "wrong")
        with self.assertRaises(AuthError): login_owner(self.c, "a@x.com", "longpassword1")  # locked

    def test_tenant_invite_flow_and_single_use(self):
        inv = create_invite(self.c, self.owner, "Ravi", "9876543210")
        with self.assertRaises(AuthError): accept_invite(self.c, inv, "1234")  # weak PIN
        s = get_session(self.c, accept_invite(self.c, inv, "4821"))
        self.assertEqual((s.role, s.pg_id), ("tenant", self.pg1))
        with self.assertRaises(AuthError): accept_invite(self.c, inv, "4821")  # reuse blocked
        get_session(self.c, login_tenant(self.c, "9876543210", "4821"))

    def test_expired_invite_and_session(self):
        inv = create_invite(self.c, self.owner, "Ravi", "9876543210")
        self.c.execute("UPDATE invites SET expires=?", (time.time() - 1,))
        with self.assertRaises(AuthError): accept_invite(self.c, inv, "4821")
        tok = login_owner(self.c, "a@x.com", "longpassword1")
        self.c.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
        with self.assertRaises(AuthError): get_session(self.c, tok)

    def test_tenant_cannot_invite_or_see_others(self):
        t = get_session(self.c, accept_invite(self.c, create_invite(self.c, self.owner, "Ravi", "9876543210"), "4821"))
        with self.assertRaises(AuthError): create_invite(self.c, t, "X", "1")
        with self.assertRaises(AuthError): scoped_get(self.c, t, "users", self.o1)   # owner's row
        self.assertEqual(scoped_get(self.c, t, "users", t.user_id)["name"], "Ravi")

    def test_pg_isolation(self):
        with self.assertRaises(AuthError): scoped_get(self.c, self.owner, "users", self.o2)  # other PG's owner
        self.assertEqual(scoped_get(self.c, self.owner, "users", self.o1)["name"], "Asha")

    def test_audit_is_append_only(self):
        n = self.c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertGreater(n, 0)
        with self.assertRaises(sqlite3.DatabaseError): self.c.execute("DELETE FROM audit_log")
        with self.assertRaises(sqlite3.DatabaseError): self.c.execute("UPDATE audit_log SET action='x'")

    def test_logout_and_no_plain_tokens(self):
        tok = login_owner(self.c, "a@x.com", "longpassword1"); logout(self.c, tok)
        with self.assertRaises(AuthError): get_session(self.c, tok)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (tok,)).fetchone()[0], 0)

if __name__ == "__main__": unittest.main()
