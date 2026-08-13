"""The dashboard server. Stdlib http.server, localhost, read-only.

Three deliberate limits:

1. It binds 127.0.0.1 unless told otherwise. A project plan names internal work,
   people and blockers; it is not something to expose by accident.
2. It serves GET only and mutates nothing. Assignment is a CLI command, so the
   dashboard cannot be made to change a plan by a link someone clicks.
3. It re-reads the plan on every request. No cache, no restart-to-refresh: the
   dashboard is a view of the files, and the files are the truth.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import render
from .model import Plan, PlanError
from .team import Team

__all__ = ["serve", "build_handler"]


def build_handler(project_root: str):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "AEF-dashboard"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # The page loads no third-party anything; say so, so a stray future
            # edit that adds a CDN link fails visibly instead of silently working.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'",
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error_page(self, message: str) -> bytes:
            return render._shell(  # noqa: SLF001 - same package, deliberate
                "AEF — plan unavailable",
                "tree",
                '<div class="headline"><h1>No readable plan</h1></div>'
                '<section class="panel problems"><h2>Cannot render</h2><ul><li>'
                + render._e(message).replace("\n", "<br>")
                + "</li></ul></section>",
                {"project": os.path.basename(os.path.abspath(project_root))},
            ).encode("utf-8")

        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path not in ("/", "/progress", "/team", "/api/plan.json"):
                self._send(404, b"not found\n", "text/plain; charset=utf-8")
                return
            with lock:
                try:
                    plan = Plan.load(project_root)
                    data = plan.as_dict()
                    # Team state is optional. A 0.3.0 project has none, and the
                    # Team view then renders as an honest "nobody registered"
                    # rather than failing the whole dashboard.
                    team = Team.load(project_root)
                    team_data = team.as_dict()
                    team_data["notices"] = team.notices(plan.tasks)
                    data["team"] = team_data
                except (PlanError, OSError, ValueError) as exc:
                    if path == "/api/plan.json":
                        self._send(503, render.json_payload({"error": str(exc)}), "application/json; charset=utf-8")
                    else:
                        self._send(503, self._error_page(str(exc)), "text/html; charset=utf-8")
                    return

            if path == "/api/plan.json":
                self._send(200, render.json_payload(data), "application/json; charset=utf-8")
            elif path == "/progress":
                self._send(200, render.progress_page(data).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/team":
                self._send(200, render.team_page(data, data.get("team") or {}).encode("utf-8"),
                           "text/html; charset=utf-8")
            else:
                self._send(200, render.tree_page(data).encode("utf-8"), "text/html; charset=utf-8")

        def log_message(self, fmt, *args):  # quiet by default
            return

    return Handler


def serve(project_root: str = ".", host: str = "127.0.0.1", port: int = 7423) -> None:
    handler = build_handler(project_root)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual = httpd.server_address[1]
    print(f"AEF dashboard  http://{host}:{actual}/")
    print(f"  project tree  http://{host}:{actual}/")
    print(f"  progress      http://{host}:{actual}/progress")
    print(f"  team          http://{host}:{actual}/team")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
