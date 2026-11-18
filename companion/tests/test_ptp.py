"""PTP framing against hand-built bytes, through a scripted transport."""
from __future__ import annotations

import struct

import pytest

from astroml_companion import ptp


class ScriptedTransport:
    """Records what the host writes and answers reads from a list of byte strings."""

    def __init__(self, replies: list[bytes]):
        self.replies = list(replies)
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def read(self, size: int) -> bytes:
        return self.replies.pop(0)


def raw(kind: int, code: int, transaction: int, payload: bytes = b"") -> bytes:
    return struct.pack("<IHHI", 12 + len(payload), kind, code, transaction) + payload


def test_command_container_bytes():
    assert ptp.container(ptp.COMMAND, 0xA570, 7, (1,)) == bytes.fromhex(
        "10000000" "0100" "70a5" "07000000" "01000000")


def test_open_session_accepts_already_open():
    transport = ScriptedTransport([raw(ptp.RESPONSE, ptp.SESSION_ALREADY_OPEN, 1)])
    session = ptp.Session(transport)
    session.open()
    assert transport.written[0] == ptp.container(ptp.COMMAND, ptp.OPEN_SESSION, 1, (1,))


def test_data_phase_split_over_reads():
    payload = bytes(range(96))
    first = raw(ptp.DATA, 0xA570, 1, payload)
    transport = ScriptedTransport([first[:40], first[40:], raw(ptp.RESPONSE, ptp.OK, 1)])
    response = ptp.Session(transport).run(0xA570, (1,))
    assert response.code == ptp.OK and response.data == payload


def test_response_params_and_skipped_event():
    params = struct.pack("<II", 1, 1)
    transport = ScriptedTransport([raw(ptp.EVENT, 0x4006, 0),
                                   raw(ptp.RESPONSE, ptp.OK, 1, params)])
    response = ptp.Session(transport).run(0xA570, (0,))
    assert response.params == (1, 1) and response.data == b""


def test_error_code_raises_when_checked():
    transport = ScriptedTransport([raw(ptp.RESPONSE, 0x2005, 1)])
    with pytest.raises(ptp.PtpError) as error:
        ptp.Session(transport).run(0x1001)
    assert error.value.code == 0x2005


def test_unchecked_error_is_returned():
    transport = ScriptedTransport([raw(ptp.RESPONSE, ptp.DEVICE_BUSY, 1)])
    assert ptp.Session(transport).run(0xA570, (1,), check=False).code == ptp.DEVICE_BUSY
