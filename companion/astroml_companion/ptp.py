"""Just enough PTP (ISO 15740) over USB bulk transfers to run vendor operations.

A transaction is a command container, an optional data phase (camera to host here), and a
response container. Containers are little-endian: length, type, code, transaction id, params.
The transport is separate so the protocol can be tested without a camera.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Protocol

COMMAND, DATA, RESPONSE, EVENT = 1, 2, 3, 4
HEADER = struct.Struct("<IHHI")
OK = 0x2001
SESSION_ALREADY_OPEN = 0x201E
DEVICE_BUSY = 0x2019
OPEN_SESSION = 0x1002
CLOSE_SESSION = 0x1003
READ_CHUNK = 64 * 1024


class PtpError(Exception):
    """The camera answered with a response code other than OK."""

    def __init__(self, operation: int, code: int):
        super().__init__(f"operation 0x{operation:04X} failed with response 0x{code:04X}")
        self.operation = operation
        self.code = code


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, size: int) -> bytes: ...


@dataclass(frozen=True)
class Response:
    code: int
    params: tuple[int, ...]
    data: bytes


def container(kind: int, code: int, transaction: int, params: tuple[int, ...] = ()) -> bytes:
    body = struct.pack(f"<{len(params)}I", *params)
    return HEADER.pack(HEADER.size + len(body), kind, code, transaction) + body


def parse_header(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < HEADER.size:
        raise ValueError(f"short PTP container ({len(data)} bytes)")
    return HEADER.unpack_from(data)


class Session:
    """One PTP session over a transport. Not thread-safe: use it from one thread."""

    def __init__(self, transport: Transport, session_id: int = 1):
        self.transport = transport
        self.session_id = session_id
        self.transaction = 0

    def open(self) -> None:
        self.transaction = 0
        response = self.run(OPEN_SESSION, (self.session_id,), check=False)
        if response.code not in (OK, SESSION_ALREADY_OPEN):
            raise PtpError(OPEN_SESSION, response.code)

    def close(self) -> None:
        self.run(CLOSE_SESSION, check=False)

    def run(self, operation: int, params: tuple[int, ...] = (), check: bool = True) -> Response:
        self.transaction += 1
        self.transport.write(container(COMMAND, operation, self.transaction, params))
        data = b""
        while True:
            length, kind, code, transaction, payload = self._read_container()
            if kind == DATA:
                data += payload
                continue
            if kind == RESPONSE:
                params_out = struct.unpack(f"<{len(payload) // 4}I", payload[:len(payload) // 4 * 4])
                if check and code != OK:
                    raise PtpError(operation, code)
                return Response(code, params_out, data)
            # Events and anything unexpected are skipped; the response always follows.

    def _read_container(self) -> tuple[int, int, int, int, bytes]:
        chunk = self.transport.read(READ_CHUNK)
        length, kind, code, transaction = parse_header(chunk)
        while len(chunk) < length:
            more = self.transport.read(READ_CHUNK)
            if not more:
                raise ValueError("PTP container ended early")
            chunk += more
        return length, kind, code, transaction, chunk[HEADER.size:length]
