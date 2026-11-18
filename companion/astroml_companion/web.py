"""A tiny HTTP server: the pages, a JSON API, and captive-portal redirects.

When the hotspot's DNS answers every name with the Pi's address, phones probe a known URL to
test for internet access. Answering those probes (and any foreign host name) with a redirect to
the status page makes the phone show it as the network's login page.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from .catalog import Catalog
from .plan import Plan
from .poller import Poller
from .state import SharedStatus

# Connectivity checks of Android, Apple, Windows and Firefox.
CAPTIVE_PROBES = {"/generate_204", "/gen_204", "/hotspot-detect.html",
                  "/library/test/success.html", "/connecttest.txt", "/ncsi.txt", "/redirect",
                  "/canonical.html", "/success.txt"}
PAGES = {"/": "page.html", "/index.html": "page.html", "/plan": "plan.html"}
MAX_BODY = 64 * 1024


class BadRequest(Exception):
    pass


def make_server(host: str, port: int, status: SharedStatus, poller: Poller,
                portal_host: str | None, catalog: Catalog | None = None) -> ThreadingHTTPServer:
    files = resources.files(__package__)
    pages = {path: files.joinpath(name).read_bytes() for path, name in PAGES.items()}
    catalog = catalog if catalog is not None else Catalog.load()
    session = poller.session

    def fov_from_last_solve() -> float:
        solution = status.get().solution
        return solution.fov_deg if solution is not None and solution.valid else 0.0

    def plan_from(body: dict) -> Plan:
        body = dict(body)
        name = str(body.pop("target", "") or "").strip()
        if name and ("ra_deg" not in body or "dec_deg" not in body):
            found = catalog.lookup(name)
            if found is None:
                raise BadRequest(f"unknown target '{name}'; enter RA/Dec instead")
            designation, ra, dec, common = found
            body.update(ra_deg=ra, dec_deg=dec,
                        name=f"{designation} {common}".strip() if common else designation)
        if "ra_deg" not in body or "dec_deg" not in body:
            raise BadRequest("give a target name or RA and Dec")
        try:
            return Plan.from_json(body)
        except TypeError as error:
            raise BadRequest(str(error)) from error

    def post_plan(body: dict) -> dict:
        plan = plan_from(body)
        try:
            panel_list = session.set_plan(plan, fov_from_last_solve())
        except ValueError as error:
            raise BadRequest(str(error)) from error
        return {"plan": plan.to_json(), "panels": [p.to_json() for p in panel_list]}

    def session_control(action: str) -> dict:
        controls = {"start": session.start, "resume": session.start, "pause": session.pause,
                    "stop": session.stop, "skip": session.skip_framing}
        if action not in controls:
            raise KeyError(action)
        try:
            controls[action]()
        except ValueError as error:
            raise BadRequest(str(error)) from error
        return session.as_json()

    class Handler(BaseHTTPRequestHandler):
        server_version = "AstroML"

        def log_message(self, format, *args):  # keep the journal quiet
            pass

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _redirect_home(self) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"http://{portal_host or self.headers.get('Host', '')}/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _foreign_host(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return bool(portal_host) and host not in (portal_host, "")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise BadRequest("request too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError as error:
                raise BadRequest(f"invalid JSON: {error}") from error
            if not isinstance(data, dict):
                raise BadRequest("expected a JSON object")
            return data

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in CAPTIVE_PROBES or (self._foreign_host() and not path.startswith("/api/")):
                self._redirect_home()
            elif path in pages:
                self._send(HTTPStatus.OK, pages[path], "text/html; charset=utf-8")
            elif path == "/api/status":
                self._json(HTTPStatus.OK, {**status.as_json(), "session": session.as_json()})
            elif path == "/api/catalog":
                self._json(HTTPStatus.OK, {"entries": len(catalog)})
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/solve":
                    poller.request_solve()
                    self._json(HTTPStatus.ACCEPTED, {"queued": True})
                elif path == "/api/expose":
                    seconds = float(self._body().get("seconds", 0))
                    if not 1 <= seconds <= 1800:
                        raise BadRequest("exposure must be 1..1800 s")
                    poller.request_exposure(seconds)
                    self._json(HTTPStatus.ACCEPTED, {"queued": True})
                elif path == "/api/cancel":
                    poller.request_cancel()
                    self._json(HTTPStatus.ACCEPTED, {"queued": True})
                elif path == "/api/plan":
                    self._json(HTTPStatus.OK, post_plan(self._body()))
                elif path.startswith("/api/session/"):
                    self._json(HTTPStatus.OK, session_control(path.rsplit("/", 1)[1]))
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            except KeyError:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            except (BadRequest, ValueError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    return ThreadingHTTPServer((host, port), Handler)
