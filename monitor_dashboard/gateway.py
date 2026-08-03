from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)
FORWARDED_HEADERS = {
    "cache-control",
    "content-disposition",
    "content-length",
    "content-type",
    "last-modified",
}


def parse_allowed_networks(
    values: list[str],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Return explicitly configured client networks or the private defaults."""
    if not values:
        return DEFAULT_PRIVATE_NETWORKS
    return tuple(ipaddress.ip_network(value, strict=False) for value in values)


class GatewayHandler(BaseHTTPRequestHandler):
    upstreams: dict[str, dict[str, str]] = {}
    allowed_networks = DEFAULT_PRIVATE_NETWORKS
    auth_user = ""
    auth_password = ""
    upstream_timeout = 30.0

    def do_GET(self) -> None:  # noqa: N802
        if not self._client_allowed():
            self._json(
                {"error": "This gateway only accepts private-network clients"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        if not self._authenticated():
            self._request_authentication()
            return

        parsed = urlparse(self.path)
        if parsed.path == "/runtime-config.js":
            self._runtime_config()
            return
        if parsed.path == "/api/gateway-health":
            self._json(
                {
                    "status": "ok",
                    "mode": "lan_gateway",
                    "servers": list(self.upstreams),
                    "authentication": bool(self.auth_user),
                }
            )
            return
        if parsed.path.startswith("/collector/"):
            self._proxy(parsed)
            return
        if parsed.path.startswith("/api/"):
            self._json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        self._static(parsed.path)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[gateway] {self.address_string()} {format % args}", flush=True)

    def _client_allowed(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.allowed_networks)

    def _authenticated(self) -> bool:
        if not self.auth_user:
            return True
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(username, self.auth_user) and hmac.compare_digest(
            password, self.auth_password
        )

    def _request_authentication(self) -> None:
        body = b'{"error":"Authentication required"}'
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="AuraPilot Monitor"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _runtime_config(self) -> None:
        payload = {
            "mode": "gateway",
            "servers": [
                {
                    "id": server_id,
                    "label": config["label"],
                    "apiBase": f"/collector/{server_id}",
                }
                for server_id, config in self.upstreams.items()
            ],
        }
        source = (
            "window.AURA_MONITOR_RUNTIME = "
            + json.dumps(payload, ensure_ascii=False)
            + ";\n"
        )
        body = source.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, parsed: object) -> None:
        path = parsed.path
        server_id = path[len("/collector/") :].split("/", 1)[0]
        upstream = self.upstreams.get(server_id)
        prefix = f"/collector/{server_id}"
        if upstream is None or not path.startswith(prefix + "/api/"):
            self._json({"error": "Unknown monitoring server"}, status=HTTPStatus.NOT_FOUND)
            return

        remainder = path[len(prefix) :]
        target = upstream["url"] + remainder
        if parsed.query:
            target += "?" + parsed.query
        request = Request(target, method="GET", headers={"Accept": self.headers.get("Accept", "*/*")})
        try:
            response = urlopen(request, timeout=self.upstream_timeout)
        except HTTPError as exc:
            response = exc
        except (URLError, TimeoutError, OSError) as exc:
            self._json(
                {"error": f"{upstream['label']} collector unavailable: {exc}"},
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        with response:
            self.send_response(response.status)
            for name, value in response.headers.items():
                if name.lower() in FORWARDED_HEADERS:
                    self.send_header(name, value)
            self.send_header("X-Monitor-Server", server_id)
            self.end_headers()
            try:
                while chunk := response.read(1024 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _static(self, path: str) -> None:
        name = path.lstrip("/") or "index.html"
        candidate = (STATIC_ROOT / name).resolve()
        if candidate != STATIC_ROOT and STATIC_ROOT not in candidate.parents:
            self._json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            candidate = STATIC_ROOT / "index.html"
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="AuraPilot monitoring gateway")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--ln-url", default="http://127.0.0.1:8765")
    parser.add_argument("--huoshan-url", default="http://127.0.0.1:8766")
    parser.add_argument("--auth-user", default="")
    parser.add_argument("--auth-password-env", default="AURAPILOT_MONITOR_PASSWORD")
    parser.add_argument(
        "--allow-cidr",
        action="append",
        default=[],
        metavar="CIDR",
        help=(
            "client network allowed to use the gateway; repeat for multiple networks "
            "(defaults to loopback and private networks)"
        ),
    )
    parser.add_argument("--upstream-timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        allowed_networks = parse_allowed_networks(args.allow_cidr)
    except ValueError as exc:
        parser.error(f"invalid --allow-cidr value: {exc}")

    password = os.environ.get(args.auth_password_env, "") if args.auth_user else ""
    if args.auth_user and not password:
        parser.error(
            f"--auth-user requires a password in environment variable {args.auth_password_env}"
        )

    GatewayHandler.upstreams = {
        "ln": {"label": "LN", "url": args.ln_url.rstrip("/")},
        "huoshan": {
            "label": "Huoshan A800",
            "url": args.huoshan_url.rstrip("/"),
        },
    }
    GatewayHandler.auth_user = args.auth_user
    GatewayHandler.auth_password = password
    GatewayHandler.allowed_networks = allowed_networks
    GatewayHandler.upstream_timeout = args.upstream_timeout
    server = ThreadingHTTPServer((args.host, args.port), GatewayHandler)
    server.daemon_threads = True
    print(
        f"AuraPilot gateway: http://{args.host}:{args.port} "
        f"(authentication: {'enabled' if args.auth_user else 'disabled'})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
