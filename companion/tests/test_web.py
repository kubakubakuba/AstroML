"""The page, the JSON API and the captive-portal redirects, on a fake camera."""
from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from astroml_companion.camera import FakeCamera
from astroml_companion.catalog import Catalog
from astroml_companion.poller import Poller
from astroml_companion.state import SharedStatus
from astroml_companion.web import make_server


@pytest.fixture()
def server():
    status = SharedStatus()
    poller = Poller(FakeCamera(), status, interval=0.05)
    catalog = Catalog({"M42": [83.8187, -5.3897, "Great Orion Nebula"]})
    httpd = make_server("127.0.0.1", 0, status, poller, portal_host="10.42.0.1",
                        catalog=catalog)
    poller.start()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not status.get().connected and time.monotonic() < deadline:
        time.sleep(0.02)
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    poller.stop()


def request(port, method, path, host="10.42.0.1", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body).encode() if body is not None else None
    connection.request(method, path, body=payload,
                       headers={"Host": host, "Content-Type": "application/json"})
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_page(server):
    code, headers, body = request(server, "GET", "/")
    assert code == 200 and b"<title>AstroML</title>" in body


def test_status_json(server):
    code, _, body = request(server, "GET", "/api/status")
    status = json.loads(body)
    assert code == 200 and status["connected"] and status["camera"] == "6D (fake)"
    assert status["solution"]["ra"].startswith("05h4") and status["reference"]["matched"] == 18


@pytest.mark.parametrize("path,host", [("/generate_204", "connectivitycheck.gstatic.com"),
                                       ("/hotspot-detect.html", "captive.apple.com"),
                                       ("/connecttest.txt", "www.msftconnecttest.com"),
                                       ("/anything", "example.com")])
def test_captive_probes_redirect_to_the_page(server, path, host):
    code, headers, _ = request(server, "GET", path, host=host)
    assert code == 302 and headers["Location"] == "http://10.42.0.1/"


def test_solve_request(server):
    code, _, body = request(server, "POST", "/api/solve")
    assert code == 202 and json.loads(body)["queued"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = json.loads(request(server, "GET", "/api/status")[2])
        if status["solution"]["sequence"] == 1:
            break
        time.sleep(0.05)
    assert status["solution"]["sequence"] == 1


def test_plan_page_and_plan_by_name(server):
    assert request(server, "GET", "/plan")[0] == 200
    code, _, body = request(server, "POST", "/api/plan",
                            body={"target": "m42", "columns": 2, "frames": 3, "exposure_s": 30})
    data = json.loads(body)
    assert code == 200 and data["plan"]["name"] == "M42 Great Orion Nebula"
    assert len(data["panels"]) == 2


def test_plan_errors_are_reported(server):
    code, _, body = request(server, "POST", "/api/plan", body={"target": "nowhere"})
    assert code == 400 and "unknown target" in json.loads(body)["error"]
    code, _, body = request(server, "POST", "/api/plan", body={"ra_deg": 1, "dec_deg": 99})
    assert code == 400 and "declination" in json.loads(body)["error"]


def test_session_controls_and_camera_status(server):
    request(server, "POST", "/api/plan", body={"ra_deg": 83.8, "dec_deg": -5.4, "frames": 1,
                                               "exposure_s": 5})
    code, _, body = request(server, "POST", "/api/session/start")
    assert code == 200 and json.loads(body)["phase"] == "framing"
    status = json.loads(request(server, "GET", "/api/status")[2])
    assert status["camera_status"]["liveview_ready"] is True
    assert request(server, "POST", "/api/session/bogus")[0] == 404


def test_test_exposure_is_range_checked(server):
    assert request(server, "POST", "/api/expose", body={"seconds": 0})[0] == 400
    assert request(server, "POST", "/api/expose", body={"seconds": 2})[0] == 202
