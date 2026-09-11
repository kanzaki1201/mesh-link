import json
import socket
import struct

import pytest

import unity_link.transport as transport
from unity_link.transport import Transport, encode_frame, read_frame


def test_framing_round_trip():
    header = {"type": "mesh_full", "name": "顔", "binary_size": 4}
    left, right = socket.socketpair()
    with left, right:
        left.sendall(encode_frame(header, b"mesh"))
        assert read_frame(right) == (header, b"mesh")


def test_fragmented_frame():
    header = {"type": "mesh_delta", "binary_size": 3}
    link = Transport("localhost", 1)
    pending = bytearray()
    frame = encode_frame(header, b"abc")
    for byte in frame[:-1]:
        pending.append(byte)
        link._accept(pending)
        assert link.poll() == []
    pending.append(frame[-1])
    link._accept(pending)
    assert link.poll() == [(header, b"abc")]
    assert pending == b""


@pytest.mark.parametrize("header,payload", [
    ([], b""),
    ({"type": 7}, b""),
    ({"type": "mesh_full"}, b"a"),
    ({"type": "mesh_full", "binary_size": 2}, b"a"),
])
def test_invalid_header(header, payload):
    with pytest.raises(ValueError):
        encode_frame(header, payload)


@pytest.mark.parametrize("json_size,binary_size", [
    (transport.MAX_JSON_BYTES + 1, 0),
    (1, transport.MAX_QUEUE_BYTES),
])
def test_invalid_frame_lengths(json_size, binary_size):
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack(">II", json_size, binary_size))
        with pytest.raises(ValueError):
            read_frame(right)


def test_invalid_incoming_binary_size():
    header = json.dumps({"type": "mesh_full", "binary_size": 1}).encode()
    link = Transport("localhost", 1)
    frame = bytearray(struct.pack(">II", len(header), 2) + header + b"ab")
    with pytest.raises(ValueError, match="binary_size"):
        link._accept(frame)


def test_overflow_drops_all_queued_deltas_and_preserves_full():
    link = Transport("localhost", 1, max_queue_bytes=300)
    full = {"type": "mesh_full", "mesh_id": "base"}
    assert link.send(full)
    assert link.send({"type": "mesh_delta", "mesh_id": "a"})
    assert link.send({"type": "mesh_delta", "mesh_id": "b"})
    assert not link.send({"type": "mesh_delta", "mesh_id": "c", "binary_size": 250}, b"x" * 250)
    assert link.take_overflow() == {"a", "b", "c"}
    assert link.take_overflow() == set()
    assert link._out_bytes == len(encode_frame(full))
    assert link._next_frame() == encode_frame(full)


def test_inflight_frame_counts_towards_limit():
    header = {"type": "mesh_delta", "mesh_id": "a"}
    frame = encode_frame(header)
    link = Transport("localhost", 1, max_queue_bytes=len(frame))
    assert link.send(header)
    assert link._next_frame() == frame
    assert not link.send({"type": "mesh_delta", "mesh_id": "b"})
    assert link._out_bytes == len(frame)
    assert link.take_overflow() == {"b"}
    link.close()
    assert link._out_bytes == 0
    assert link.poll() == []


def test_oversized_full_fails_instead_of_retrying():
    link = Transport("localhost", 1, max_queue_bytes=32)
    with pytest.raises(ValueError, match="Outgoing frame exceeds"):
        link.send({"type": "mesh_full", "mesh_id": "too-large"})


def test_listener_error_survives_immediate_eof(monkeypatch):
    left, right = socket.socketpair()
    error = {"type": "error", "message": "Pairing rejected"}
    right.sendall(encode_frame(error))
    right.close()
    monkeypatch.setattr(transport.socket, "create_connection", lambda *a, **kw: left)
    link = Transport("localhost", 1)
    link._run()
    messages = link.poll()
    assert messages[0] == (error, b"")
    assert messages[-1][0]["type"] == "error"
    assert link._stop.is_set()
