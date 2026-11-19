"""HTTP server of the platform: routing, JSON in and out, static files. No business logic here."""

from __future__ import annotations

import json
import re
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import unquote, urlsplit

from parco.core.errors import FilesModifiedError, NotFoundError, ParcoError, PhaseError
from parco.core.project import Project
from parco.server.api import Api, StaleDocumentError
from parco.server.figures import FigureError

Handler = Callable[[Api, re.Match[str], dict[str, Any]], Any]

ROUTES: list[tuple[str, re.Pattern[str], Handler]] = [
    ("GET", re.compile(r"/api/state"), lambda api, m, body: api.state()),
    ("GET", re.compile(r"/api/document"), lambda api, m, body: api.document()),
    ("GET", re.compile(r"/api/revision"), lambda api, m, body: api.revision()),
    ("POST", re.compile(r"/api/edits/(\w+)/(accept|reject|reset)"), lambda api, m, body: api.set_edit_status(m[1], m[2])),
    ("POST", re.compile(r"/api/edits/(\w+)/discuss"), lambda api, m, body: api.discuss_edit(m[1], body)),
    ("POST", re.compile(r"/api/threads"), lambda api, m, body: api.create_thread(body)),
    ("POST", re.compile(r"/api/threads/(\w+)/reply"), lambda api, m, body: api.reply(m[1], body)),
    ("POST", re.compile(r"/api/threads/(\w+)/(resolve|reopen)"), lambda api, m, body: api.set_thread_status(m[1], m[2])),
    ("POST", re.compile(r"/api/close"), lambda api, m, body: api.close()),
]
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
_STATIC_NAME = re.compile(r"[\w.-]+")


def error_status(error: ParcoError) -> HTTPStatus:
    """HTTP status of a core error."""
    if isinstance(error, NotFoundError):
        return HTTPStatus.NOT_FOUND
    if isinstance(error, (PhaseError, FilesModifiedError, StaleDocumentError)):
        return HTTPStatus.CONFLICT
    return HTTPStatus.BAD_REQUEST


class _BadRequest(ParcoError):
    """Malformed HTTP request body."""


class RequestHandler(BaseHTTPRequestHandler):
    """Dispatches requests to the API (`api` is set on the subclass built by `make_server`)."""

    api: Api

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        """Serve the API, figures and static files."""
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self._dispatch("GET", path)
        elif path.startswith("/fig/"):
            self._send_figure(path.removeprefix("/fig/"))
        else:
            self._send_static("index.html" if path == "/" else path.removeprefix("/static/"))

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        """Serve the API."""
        self._dispatch("POST", urlsplit(self.path).path)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (signature of the base class)
        """Silence per-request logging (the AI and the user share the terminal)."""

    def _dispatch(self, method: str, path: str) -> None:
        for route_method, pattern, handler in ROUTES:
            match = pattern.fullmatch(path)
            if match and route_method == method:
                self._run(handler, match)
                return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"No route for {method} {path}"})

    def _run(self, handler: Handler, match: re.Match[str]) -> None:
        try:
            result = handler(self.api, match, self._read_json())
        except ParcoError as exc:
            self._send_json(error_status(exc), {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 (report any failure to the page instead of dropping it)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})
        else:
            self._send_json(HTTPStatus.OK, result)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise _BadRequest("Invalid Content-Length header.") from exc
        if length < 0:
            raise _BadRequest("Invalid Content-Length header.")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise _BadRequest(f"Invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise _BadRequest("The JSON body must be an object.")
        return data

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, name: str) -> None:
        resource = files("parco.server").joinpath("static", name)
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if not _STATIC_NAME.fullmatch(name) or suffix not in STATIC_TYPES or not resource.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {name}"})
            return
        self._send(HTTPStatus.OK, resource.read_bytes(), STATIC_TYPES[suffix])

    def _send_figure(self, name: str) -> None:
        try:
            found = self.api.figure(unquote(name))
        except FigureError as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if found is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown figure: {name}"})
            return
        path, content_type = found
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)


def make_server(project: Project, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Build the HTTP server of a project (port 0 picks a free port)."""
    handler = type("ProjectRequestHandler", (RequestHandler,), {"api": Api(project)})
    return ThreadingHTTPServer((host, port), handler)


def serve(project: Project, port: int, open_browser: bool) -> None:
    """Serve the platform until interrupted (Ctrl-C)."""
    server = make_server(project, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Parco review platform at {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
