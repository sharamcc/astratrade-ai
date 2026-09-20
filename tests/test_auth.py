import unittest
from datetime import timedelta

from astratrade.auth import SessionAuth
from astratrade.domain import User
from astratrade.repository import Repository


class SessionAuthTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository.in_memory()
        self.repository.save_user(User("user-1", "user@example.com"))
        self.auth = SessionAuth(self.repository)

    def tearDown(self):
        self.repository.close()

    def test_issue_and_resolve_opaque_bearer_token(self):
        token = self.auth.issue("user-1")
        self.assertEqual(self.auth.resolve("Bearer " + token), "user-1")
        self.assertIsNone(self.auth.resolve(token))
        self.assertIsNone(self.auth.resolve("Basic " + token))

        row = self.repository.connection.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertNotEqual(row["token_hash"], token)

    def test_revoke_invalidates_token(self):
        token = self.auth.issue("user-1")
        self.auth.revoke(token)
        self.assertIsNone(self.auth.resolve("Bearer " + token))

    def test_expired_token_is_rejected(self):
        token = self.auth.issue("user-1", timedelta(seconds=-1))
        self.assertIsNone(self.auth.resolve("Bearer " + token))

    def test_suspended_user_is_rejected(self):
        token = self.auth.issue("user-1")
        self.repository.save_user(User("user-1", "user@example.com", status="suspended"))
        self.assertIsNone(self.auth.resolve("Bearer " + token))


if __name__ == "__main__":
    unittest.main()
