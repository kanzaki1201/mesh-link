"""Nomad Link framing and one socket worker."""

from collections import deque
import json
import select
import socket
import struct
import threading

MAX_JSON_BYTES = 50 * 1024 * 1024
MAX_QUEUE_BYTES = 64 * 1024 * 1024


def _validate(header, binary_size):
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise ValueError("Frame header must be an object with a string type")
    if binary_size or "binary_size" in header:
        if type(header.get("binary_size")) is not int or header["binary_size"] != binary_size:
            raise ValueError("Frame binary_size does not match payload")


def encode_frame(header, payload=b""):
    _validate(header, len(payload))
    encoded = json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("JSON header exceeds 50 MiB")
    return struct.pack(">II", len(encoded), len(payload)) + encoded + payload


def _receive_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(min(size - len(result), 65536))
        if not chunk:
            raise ConnectionError("Listener disconnected")
        result.extend(chunk)
    return bytes(result)


def _frame_sizes(prefix, limit):
    json_size, binary_size = struct.unpack(">II", prefix)
    if json_size > MAX_JSON_BYTES:
        raise ValueError("JSON header exceeds 50 MiB")
    if 8 + json_size + binary_size > limit:
        raise ValueError("Incoming frame exceeds queue limit")
    return json_size, binary_size


def read_frame(sock):
    json_size, binary_size = _frame_sizes(_receive_exact(sock, 8), MAX_QUEUE_BYTES)
    header = json.loads(_receive_exact(sock, json_size))
    _validate(header, binary_size)
    return header, _receive_exact(sock, binary_size)


class Transport:
    def __init__(self, host, port, max_queue_bytes=MAX_QUEUE_BYTES):
        self.host, self.port = host, port
        self.max_queue_bytes = max_queue_bytes
        self._outbound = deque()
        self._inbound = deque()
        self._out_bytes = 0
        self._in_bytes = 0
        self._overflow = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Transport already started")
        self._thread = threading.Thread(target=self._run, daemon=True, name="Mesh Link")
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        with self._lock:
            self._outbound.clear()
            self._inbound.clear()
            self._overflow.clear()
            self._out_bytes = self._in_bytes = 0

    def _drop_deltas(self):
        kept = deque()
        for header, frame in self._outbound:
            if header["type"] == "mesh_delta":
                self._out_bytes -= len(frame)
                self._overflow.add(header["mesh_id"])
            else:
                kept.append((header, frame))
        self._outbound = kept

    def send(self, header, payload=b""):
        frame = encode_frame(header, payload)
        if len(frame) > self.max_queue_bytes and header["type"] != "mesh_delta":
            raise ValueError("Outgoing frame exceeds queue limit")
        with self._lock:
            if self._stop.is_set():
                return False
            if self._out_bytes + len(frame) > self.max_queue_bytes:
                self._drop_deltas()
                mesh_id = header.get("mesh_id")
                if mesh_id is not None:
                    self._overflow.add(mesh_id)
                return False
            self._outbound.append((dict(header), frame))
            self._out_bytes += len(frame)
        return True

    def send_batch(self, messages):
        frames = [(dict(header), encode_frame(header, payload))
                  for header, payload in messages]
        size = sum(len(frame) for _, frame in frames)
        with self._lock:
            if self._stop.is_set() or self._out_bytes + size > self.max_queue_bytes:
                return False
            self._outbound.extend(frames)
            self._out_bytes += size
        return True

    def poll(self):
        with self._lock:
            messages = [(header, payload) for header, payload in self._inbound]
            self._inbound.clear()
            self._in_bytes = 0
        return messages

    def take_overflow(self):
        with self._lock:
            result = self._overflow
            self._overflow = set()
        return result

    def _accept(self, buffer):
        while len(buffer) >= 8:
            json_size, binary_size = _frame_sizes(buffer[:8], self.max_queue_bytes)
            size = 8 + json_size + binary_size
            if len(buffer) < size:
                return
            header = json.loads(buffer[8:8 + json_size])
            _validate(header, binary_size)
            payload = bytes(buffer[8 + json_size:size])
            with self._lock:
                if self._in_bytes + size > self.max_queue_bytes:
                    raise ValueError("Incoming queue exceeds limit")
                self._inbound.append((header, payload))
                self._in_bytes += size
            del buffer[:size]

    def _next_frame(self):
        with self._lock:
            return self._outbound.popleft()[1] if self._outbound else b""

    def _exchange(self, sock):
        incoming = bytearray()
        outgoing = b""
        offset = 0
        while not self._stop.is_set():
            if not outgoing:
                outgoing = self._next_frame()
                offset = 0
            readable, writable, _ = select.select([sock], [sock] if outgoing else [], [], 0.02)
            if readable:
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError("Listener disconnected")
                with self._lock:
                    if self._in_bytes + len(incoming) + len(data) > self.max_queue_bytes:
                        raise ValueError("Incoming queue exceeds limit")
                incoming.extend(data)
                self._accept(incoming)
            if writable:
                offset += sock.send(memoryview(outgoing)[offset:offset + 65536])
                if offset == len(outgoing):
                    with self._lock:
                        self._out_bytes -= len(outgoing)
                    outgoing = b""

    def _run(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=2) as sock:
                sock.settimeout(0.2)
                self._exchange(sock)
        except (OSError, ValueError, UnicodeError) as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._inbound.append(({"type": "error", "message": str(exc)}, b""))
        finally:
            self._stop.set()
