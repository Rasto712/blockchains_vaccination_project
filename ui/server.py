# AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed.
"""The local web UI: a small HTTP server for the page in ui/static and its JSON endpoints under /api/.
Run from the project root after npm run compile, with npm run node running and contracts deployed (the
page can also deploy them):
    .venv/bin/python -m ui.server [--settings PATH] [--port 8000]
then open http://127.0.0.1:8000/ (or http://localhost:8000/). Ctrl+C stops it.

Demo mode, not authentication: the page picks the acting role and the server signs with that role's unlocked
Hardhat account, as the console does. The server listens on 127.0.0.1 only. It refuses a request whose Host
is not 127.0.0.1:<port> or localhost:<port> (DNS rebinding), and a POST from another Origin or without a JSON
body (cross-site forms). Every response forbids framing, inline code and caching. POST actions, the guided
demo's steps and the demo controls (ui/demo.py, under /api/demo/) share one lock, so transactions and time
moves never interleave; GET /api/state does not wait for it. The page only ever gets the
fixed texts of ui/actions.py, never exception text or a traceback, and the server log shows only the type
of a failure.
"""
import argparse
import json
import socketserver
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qs, urlsplit
from app import records
from ui import actions, demo

SETTINGS_FILE = records.PROJECT_ROOT / "config" / "settings.json"
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
STATIC_DIR = Path(__file__).resolve().parent / "static"
# the only files served; nothing from the URL is ever used as a path
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "demo.js": "text/javascript; charset=utf-8",
}
MAX_BODY_BYTES = 16 * 1024
SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)
ACTOR_LABELS = ("deployer", "clinic", "guardian", "school", "doctor")


def _post_routes() -> dict[str, Any]:
    # path -> function(server, body) returning a result; each runs under the action lock
    return {
        "/api/setup": lambda server, body: actions.perform(actions.setup, server.settings),
        "/api/deploy": lambda server, body: server.deploy(),
        "/api/register": lambda server, body: actions.perform(actions.register, server.settings, body.get("role")),
        "/api/attest": lambda server, body: actions.perform(actions.attest, server.settings, body.get("role")),
        "/api/grant": lambda server, body: actions.perform(
            actions.grant, server.settings, body.get("role"), body.get("requester"), body.get("scope"), body.get("days"),
        ),
        "/api/revoke": lambda server, body: actions.perform(
            actions.revoke, server.settings, body.get("role"), body.get("requester"), body.get("scope"),
        ),
        "/api/school-check": lambda server, body: actions.perform(actions.school_check, server.settings),
        "/api/doctor-view": lambda server, body: actions.perform(actions.doctor_view, server.settings),
        "/api/demo/start": lambda server, body: server.guided.start(server.settings, server.settings_path),
        "/api/demo/next": lambda server, body: server.guided.run_next(server.settings, server.settings_path, body.get("step")),
        "/api/demo/tamper": lambda server, body: actions.perform(demo.tamper, server.settings),
        "/api/demo/expire": lambda server, body: actions.perform(
            demo.expire, server.settings, body.get("requester"), body.get("scope"),
        ),
    }


def allowed_hosts(port: int) -> set[str]:
    """The Host values of this page: 127.0.0.1 and localhost with the port (browsers leave out port 80)."""
    hosts = {f"{HOST}:{port}", f"localhost:{port}"}
    if port == 80:
        hosts |= {HOST, "localhost"}
    return hosts


class UIServer(ThreadingHTTPServer):
    """The HTTP server with the settings, the settings file (for deploy_local) and the action lock."""

    daemon_threads = True

    def __init__(self, settings: dict[str, Any], settings_path: Path, port: int = DEFAULT_PORT) -> None:
        self.settings = settings
        self.settings_path = Path(settings_path)
        self.action_lock = threading.Lock()
        self.guided = demo.GuidedDemo()
        self.post_routes = _post_routes()
        super().__init__((HOST, port), Handler)
        self.allowed_hosts = allowed_hosts(self.server_address[1])
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    def state(self) -> dict[str, Any]:
        """The page's snapshot, with the guided demo's progress."""
        snapshot = actions.state(self.settings)
        progress = self.guided.progress()
        # the guided demo ends when its contracts are gone (a node restart, a recompile) or replaced
        # (deploy_local --reset, the scripted demo), even by contracts at the same addresses
        deployment, node = snapshot["deployment"], snapshot["node"]
        started_on = (progress.pop("contracts"), progress.pop("deploy_block"))
        replaced = deployment["deployed"] and (deployment["contracts"], deployment["deploy_block"]) != started_on
        gone = node["reachable"] and not deployment["deployed"]
        progress["stale"] = bool(progress["started"] and (replaced or gone))
        snapshot["demo"] = progress
        return snapshot

    def deploy(self) -> dict[str, Any]:
        """Fresh contracts from the page; the guided demo's progress belonged to the old ones."""
        answer = actions.perform(actions.deploy, self.settings_path)
        if answer["status"] == "ok":
            self.guided.reset()
        return answer

    def server_bind(self) -> None:
        # HTTPServer.server_bind looks up the host name, which can take seconds; the name is known
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]

    def handle_error(self, request: Any, client_address: Any) -> None:
        # a dropped connection or an error outside a handler: one line with the type, never a traceback
        print(f"ui: request failed: {sys.exc_info()[0].__name__}", file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server: UIServer
    server_version = "MyVaccinationCard"
    sys_version = ""

    def do_GET(self) -> None:
        self._handle(self._get)

    def do_POST(self) -> None:
        self._handle(self._post)

    def log_message(self, format: str, *args: Any) -> None:
        # the page polls every 2 seconds, so request lines would flood the terminal
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # http.server's own refusals (a bad request line, an unsupported method) as JSON with the same headers
        self.close_connection = True
        self._send_json(HTTPStatus(code), actions.result("invalid", f"HTTP {code}"))

    def _handle(self, route: Any) -> None:
        try:
            if self.headers.get("Host", "").lower() not in self.server.allowed_hosts:
                self._send_json(HTTPStatus.FORBIDDEN, actions.result(
                    "forbidden", f"forbidden: open the page at http://{HOST}:{self.server.server_address[1]}/",
                ))
                return
            route()
        except Exception as error:
            actions.log_failure(error)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, actions.result("failed", f"failed: {type(error).__name__}"))

    def _get(self) -> None:
        url = urlsplit(self.path)
        if url.path in ("/", "/index.html"):
            self._send_static("index.html")
        elif url.path.startswith("/static/") and url.path[len("/static/"):] in STATIC_FILES:
            self._send_static(url.path[len("/static/"):])
        elif url.path == "/favicon.ico":
            self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
        elif url.path == "/api/state":
            self._send_json(HTTPStatus.OK, self.server.state())
        elif url.path == "/api/record":
            role = parse_qs(url.query).get("role", [""])[0]
            try:
                payload = actions.perform(actions.local_record, self.server.settings, role)
            except actions.Forbidden as error:
                self._send_json(HTTPStatus.FORBIDDEN, actions.result("forbidden", str(error)))
                return
            self._send_json(HTTPStatus.OK, payload)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, actions.result("invalid", "not found"))

    def _post(self) -> None:
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in self.server.allowed_origins:
            self._send_json(HTTPStatus.FORBIDDEN, actions.result("forbidden", "forbidden: request from another site"))
            return
        route = self.server.post_routes.get(urlsplit(self.path).path)
        if route is None:
            self._send_json(HTTPStatus.NOT_FOUND, actions.result("invalid", "not found"))
            return
        # a JSON body cannot come from a plain cross-site form, and needs a preflight this server never answers
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, actions.result("invalid", "send the request as JSON"))
            return
        body = self._read_body()
        if body is None:
            return
        try:
            with self.server.action_lock:
                payload = route(self.server, body)
        except actions.InvalidRequest as error:
            self._send_json(HTTPStatus.BAD_REQUEST, actions.result("invalid", str(error)))
            return
        self._send_json(HTTPStatus.OK, payload)

    def _read_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, actions.result("invalid", "the request body is missing or too large"))
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, actions.result("invalid", "the request body must be a JSON object"))
            return None
        return body

    def _send_static(self, name: str) -> None:
        self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name])

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def read_settings(path: Path) -> dict[str, Any]:
    """Read the settings file; on a problem print one line (as the console does) and exit 1."""
    try:
        settings = json.loads(Path(path).read_bytes())
    except OSError:
        _exit(f"no settings file at {records.shown_path(Path(path))}: copy config/settings.example.json to "
              "config/settings.json, or pass --settings PATH")
    except ValueError:
        _exit("the settings file is not valid JSON")
    if not isinstance(settings, dict):
        _exit("the settings file is not a settings object")
    labels = settings.get("actor_account_indices")
    if not isinstance(labels, dict) or not all(label in labels for label in ACTOR_LABELS):
        _exit("the settings need actor_account_indices for deployer, clinic, guardian, school and doctor")
    return settings


def main(argv: list[str] | None = None) -> None:
    """Serve the page on 127.0.0.1 until Ctrl+C."""
    parser = argparse.ArgumentParser(description="Serve the My Vaccination Card web UI on 127.0.0.1")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_FILE, metavar="PATH",
        help="settings file (default: config/settings.json)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default: {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    settings = read_settings(args.settings)
    try:
        server = UIServer(settings, Path(args.settings).resolve(), args.port)
    except (OSError, OverflowError):
        _exit(f"cannot listen on {HOST}:{args.port}: the port is in use or invalid; pick another with --port")
    print(f"My Vaccination Card UI: http://{HOST}:{server.server_address[1]}/  (Ctrl+C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def _exit(message: str) -> NoReturn:
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
