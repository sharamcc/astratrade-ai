import unittest
from datetime import datetime, timedelta, timezone

from astratrade.domain import ConnectionStatus, OAuthConnection, OAuthTokenSet, User
from astratrade.oauth import OAuthService
from astratrade.repository import Repository


class FakeOAuthClient:
    provider = "okx-test"

    def __init__(self):
        self.exchanged_codes = []

    def authorization_url(self, state, redirect_uri, scopes):
        return "https://okx.example/authorize?state=%s" % state

    def exchange_code(self, code, redirect_uri):
        self.exchanged_codes.append((code, redirect_uri))
        return OAuthTokenSet(
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=frozenset({"read", "trade"}),
        )


class FakeProtector:
    def seal(self, plaintext):
        return "sealed:" + plaintext


class OAuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository.in_memory()
        self.repo.save_user(User("user-1", "user@example.com", risk_confirmed=True))
        self.client = FakeOAuthClient()
        self.service = OAuthService(self.repo, self.client, FakeProtector())

    def tearDown(self):
        self.repo.close()

    def test_state_is_bound_to_https_redirect_and_single_use(self):
        start = self.service.begin(
            "user-1", "https://app.example/oauth/callback", {"read", "trade"}
        )
        connection = self.service.complete(
            start.state, "one-time-code", "https://app.example/oauth/callback"
        )

        self.assertEqual(connection.status, ConnectionStatus.ACTIVE)
        self.assertEqual(connection.provider, "okx-test")
        self.assertEqual(connection.encrypted_access_token, "sealed:access-secret")
        self.assertEqual(connection.encrypted_refresh_token, "sealed:refresh-secret")
        self.assertEqual(
            self.client.exchanged_codes,
            [("one-time-code", "https://app.example/oauth/callback")],
        )
        self.assertEqual(self.repo.audit_count(connection.connection_id), 1)

        with self.assertRaises(ValueError):
            self.service.complete(
                start.state, "replay-code", "https://app.example/oauth/callback"
            )

    def test_redirect_mismatch_consumes_state_without_exchanging_code(self):
        start = self.service.begin(
            "user-1", "https://app.example/oauth/callback", {"read"}
        )
        with self.assertRaises(ValueError):
            self.service.complete(start.state, "code", "https://evil.example/callback")
        self.assertEqual(self.client.exchanged_codes, [])

    def test_non_https_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.begin("user-1", "http://app.example/callback", {"read"})

    def test_revoke_marks_connection_and_audits(self):
        start = self.service.begin(
            "user-1", "https://app.example/oauth/callback", {"read"}
        )
        connection = self.service.complete(
            start.state, "code", "https://app.example/oauth/callback"
        )
        self.service.revoke("user-1")

        stored = self.repo.get_exchange_connection("user-1")
        self.assertEqual(stored.status, ConnectionStatus.REVOKED)
        self.assertEqual(self.repo.audit_count(connection.connection_id), 2)

    def test_expired_connection_is_not_usable(self):
        expired = OAuthConnection(
            connection_id="expired-1",
            user_id="user-1",
            provider="okx-test",
            status=ConnectionStatus.ACTIVE,
            encrypted_access_token="sealed:old-access",
            encrypted_refresh_token="sealed:old-refresh",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            scopes=frozenset({"read", "trade"}),
        )
        self.repo.save_exchange_connection(expired)

        self.assertFalse(self.service.has_valid_connection("user-1"))
        self.assertEqual(
            self.repo.get_exchange_connection("user-1").status,
            ConnectionStatus.EXPIRED,
        )


if __name__ == "__main__":
    unittest.main()
