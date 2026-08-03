from __future__ import annotations

import base64
import ipaddress
import json
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from monitor_dashboard.gateway import (
    DEFAULT_PRIVATE_NETWORKS,
    GatewayHandler,
    parse_allowed_networks,
)


class UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send_json({"status": "ok", "server_id": "mock"})
            return
        if self.path == "/api/projects":
            self._send_json({"projects": [{"name": "demo"}]})
            return
        if self.path == "/api/file?download=1&path=demo.csv":
            body = b"name\ndemo\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="demo.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "missing"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send_json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_port = self.upstream.server_address[1]
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()

        GatewayHandler.upstreams = {
            "ln": {"label": "LN", "url": f"http://127.0.0.1:{upstream_port}"},
            "huoshan": {
                "label": "Huoshan A800",
                "url": f"http://127.0.0.1:{upstream_port}",
            },
        }
        GatewayHandler.auth_user = ""
        GatewayHandler.auth_password = ""
        GatewayHandler.allowed_networks = DEFAULT_PRIVATE_NETWORKS
        GatewayHandler.upstream_timeout = 2
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        self.gateway.daemon_threads = True
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever,
            daemon=True,
        )
        self.gateway_thread.start()
        self.base_url = f"http://127.0.0.1:{self.gateway.server_address[1]}"

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.gateway_thread.join(timeout=2)
        self.upstream_thread.join(timeout=2)

    def test_runtime_config_uses_same_origin_gateway_routes(self) -> None:
        with urlopen(f"{self.base_url}/runtime-config.js", timeout=2) as response:
            source = response.read().decode("utf-8")

        self.assertIn('"mode": "gateway"', source)
        self.assertIn('"apiBase": "/collector/ln"', source)
        self.assertIn('"apiBase": "/collector/huoshan"', source)

    def test_gateway_proxies_json_and_preserves_server_identity(self) -> None:
        with urlopen(f"{self.base_url}/collector/ln/api/projects", timeout=2) as response:
            payload = json.load(response)
            server_id = response.headers["X-Monitor-Server"]

        self.assertEqual(payload, {"projects": [{"name": "demo"}]})
        self.assertEqual(server_id, "ln")

    def test_gateway_streams_download_headers_and_body(self) -> None:
        url = f"{self.base_url}/collector/huoshan/api/file?download=1&path=demo.csv"
        with urlopen(url, timeout=2) as response:
            body = response.read()
            disposition = response.headers["Content-Disposition"]

        self.assertEqual(body, b"name\ndemo\n")
        self.assertEqual(disposition, 'attachment; filename="demo.csv"')

    def test_optional_basic_auth_rejects_missing_credentials(self) -> None:
        GatewayHandler.auth_user = "monitor"
        GatewayHandler.auth_password = "secret"
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/collector/ln/api/health", timeout=2)
        self.assertEqual(context.exception.code, HTTPStatus.UNAUTHORIZED)

        token = base64.b64encode(b"monitor:secret").decode("ascii")
        request = Request(
            f"{self.base_url}/collector/ln/api/health",
            headers={"Authorization": f"Basic {token}"},
        )
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "ok")

    def test_allowed_networks_default_to_private_clients(self) -> None:
        networks = parse_allowed_networks([])

        self.assertTrue(any(ipaddress.ip_address("192.168.1.5") in network for network in networks))
        self.assertFalse(any(ipaddress.ip_address("8.8.8.8") in network for network in networks))

    def test_public_cidr_can_be_enabled_explicitly(self) -> None:
        networks = parse_allowed_networks(["0.0.0.0/0", "::/0"])

        self.assertTrue(any(ipaddress.ip_address("8.8.8.8") in network for network in networks))
        self.assertTrue(any(ipaddress.ip_address("2001:4860:4860::8888") in network for network in networks))


if __name__ == "__main__":
    unittest.main()
