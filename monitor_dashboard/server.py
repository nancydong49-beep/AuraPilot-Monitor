from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .monitor import ProjectMonitor


STATIC_ROOT = Path(__file__).resolve().parent / "static"


class MonitorHandler(BaseHTTPRequestHandler):
    monitor: ProjectMonitor
    server_id = "local"
    server_label = "Local"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/runtime-config.js":
                self._javascript(
                    "window.AURA_MONITOR_RUNTIME = "
                    + json.dumps({"mode": "collector"}, ensure_ascii=False)
                    + ";\n"
                )
                return
            if parsed.path == "/api/health":
                self._json(
                    {
                        "status": "ok",
                        "server_id": self.server_id,
                        "server_label": self.server_label,
                        "project_root": str(self.monitor.project_root),
                    }
                )
                return
            if parsed.path == "/api/projects":
                self._json({"projects": self.monitor.list_projects()})
                return
            if parsed.path == "/api/file":
                query = parse_qs(parsed.query)
                value = query.get("path", [""])[0]
                if query.get("download", ["0"])[0] == "1":
                    self._download(self.monitor.resolve_download(value))
                else:
                    self._json(self.monitor.preview_file(value))
                return

            segments = [unquote(item) for item in parsed.path.split("/") if item]
            if len(segments) == 4 and segments[:2] == ["api", "projects"] and segments[3] == "runs":
                self._json({"runs": self.monitor.list_runs(segments[2])})
                return
            if (
                len(segments) == 6
                and segments[:2] == ["api", "projects"]
                and segments[3] == "runs"
                and segments[5] == "status"
            ):
                self._json(self.monitor.get_run_status(segments[2], segments[4]))
                return

            if parsed.path.startswith("/api/"):
                self._json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._static(parsed.path)
        except FileNotFoundError as exc:
            self._json({"error": f"Not found: {exc}"}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # keep the dashboard available for partial NFS failures
            self._json(
                {"error": f"{exc.__class__.__name__}: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: object) -> None:
        print(f"[monitor] {self.address_string()} {format % args}", flush=True)

    def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _javascript(self, source: str) -> None:
        body = source.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        name = path.lstrip("/") or "index.html"
        candidate = (STATIC_ROOT / name).resolve()
        if candidate != STATIC_ROOT and STATIC_ROOT not in candidate.parents:
            raise FileNotFoundError(name)
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, path: Path) -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                self.wfile.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="AuraPilot read-only monitoring dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-root", default="/nfs/project")
    parser.add_argument("--server-id", default="local")
    parser.add_argument("--server-label", default="Local")
    args = parser.parse_args()

    MonitorHandler.monitor = ProjectMonitor(args.project_root)
    MonitorHandler.server_id = args.server_id
    MonitorHandler.server_label = args.server_label
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(
        f"AuraPilot monitor: http://{args.host}:{args.port} "
        f"(projects: {MonitorHandler.monitor.project_root})",
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
