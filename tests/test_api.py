import unittest
from datetime import datetime, timedelta, timezone

from astratrade.api import ApplicationAPI, Request
from astratrade.domain import OAuthTokenSet, User
from astratrade.oauth import OAuthService
from astratrade.repository import Repository


class FakeOAuthClient:
    provider = "okx-test"

    def authorization_url(self, state, redirect_uri, scopes):
        return "https://okx.example/authorize?state=%s" % state

    def exchange_code(self, code, redirect_uri):
        return OAuthTokenSet(
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=frozenset({"read", "trade"}),
        )


class FakeProtector:
    def seal(self, plaintext):
        return "sealed:" + plaintext


class ApplicationAPITests(unittest.TestCase):
    def setUp(self):
        repository = Repository.in_memory()
        repository.save_user(User("user-1", "user@example.com", risk_confirmed=True))
        service = OAuthService(repository, FakeOAuthClient(), FakeProtector())
        self.repository = repository
        self.api = ApplicationAPI(service, "https://app.example/oauth/callback")

    def tearDown(self):
        self.repository.close()

    def test_start_callback_and_connection_status(self):
        start = self.api.handle(
            Request(
                "POST",
                "/v1/oauth/start",
                user_id="user-1",
                body={
                    "redirect_uri": "https://app.example/oauth/callback",
                    "scopes": ["read", "trade"],
                },
            )
        )
        self.assertEqual(start.status, 200)
        self.assertIn("state", start.body)

        callback = self.api.handle(
            Request(
                "GET",
                "/v1/oauth/callback",
                query={
                    "state": start.body["state"],
                    "code": "one-time-code",
                },
            )
        )
        self.assertEqual(callback.status, 200)
        self.assertEqual(callback.body["status"], "active")

        status = self.api.handle(Request("GET", "/v1/connection", user_id="user-1"))
        self.assertEqual(status.status, 200)
        self.assertTrue(status.body["connected"])

    def test_authenticated_identity_cannot_come_from_body(self):
        response = self.api.handle(
            Request(
                "POST",
                "/v1/oauth/start",
                body={
                    "user_id": "user-1",
                    "redirect_uri": "https://app.example/oauth/callback",
                    "scopes": ["read"],
                },
            )
        )
        self.assertEqual(response.status, 400)
        self.assertIn("authenticated user", response.body["error"])

    def test_revoke_requires_authenticated_user(self):
        response = self.api.handle(Request("POST", "/v1/oauth/revoke"))
        self.assertEqual(response.status, 400)

    def test_unknown_route_is_not_exposed(self):
        response = self.api.handle(Request("GET", "/v1/admin/tokens", user_id="user-1"))
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
