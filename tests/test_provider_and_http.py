import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from astratrade.api import ApplicationAPI
from astratrade.http_server import create_server
from astratrade.okx_oauth import OKXOAuthClient, OKXOAuthConfig


class ProviderAndHttpTests(unittest.TestCase):
    def test_authorization_url_uses_configured_endpoint_and_state(self):
        client = OKXOAuthClient(
            OKXOAuthConfig(
                "client-id",
                "client-secret",
                "https://oauth.example/authorize",
                "https://oauth.example/token",
            )
        )
        url = client.authorization_url(
            "state-value", "https://app.example/callback", {"trade", "read"}
        )
        self.assertIn("client_id=client-id", url)
        self.assertIn("state=state-value", url)
        self.assertIn("scope=read+trade", url)

    def test_config_requires_explicit_endpoints_and_secrets(self):
        with self.assertRaises(ValueError):
            OKXOAuthConfig.from_env({})

    @patch("urllib.request.urlopen")
    def test_token_response_is_normalized(self, urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "expires_in": 3600,
                        "scope": "read trade",
                    }
                ).encode()

        urlopen.return_value = FakeResponse()
        client = OKXOAuthClient(
            OKXOAuthConfig(
                "client-id",
                "client-secret",
                "https://oauth.example/authorize",
                "https://oauth.example/token",
            )
        )
        tokens = client.exchange_code("code", "https://app.example/callback")
        self.assertEqual(tokens.access_token, "access")
        self.assertEqual(tokens.scopes, frozenset({"read", "trade"}))
        self.assertGreater(tokens.expires_at, datetime.now(timezone.utc))
        self.assertTrue(urlopen.called)

    def test_http_server_requires_injected_auth_resolver(self):
        class FakeAPI:
            def handle(self, request):
                from astratrade.api import Response

                return Response(200, {"user_id": request.user_id})

        server = create_server(FakeAPI(), lambda _: None, port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
