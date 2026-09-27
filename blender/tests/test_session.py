import queue
import socketserver
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from mesh_link.encode import encode_mesh_delta, encode_mesh_full
from mesh_link.session import Session, _bake_messages, channel_key
from mesh_link.transport import encode_frame, read_frame


@contextmanager
def listener():
    incoming = queue.Queue()
    connection = queue.Queue()
    release = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            connection.put(self.request)
            try:
                while not release.is_set():
                    incoming.put(read_frame(self.request))
            except (EOFError, OSError):
                pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = Session(*server.server_address, pair_token="stored")
    session.connect()
    sock = connection.get(timeout=3)
    try:
        yield session, sock, incoming
    finally:
        release.set()
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def receive_until(session, condition):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        session.drain()
        if condition():
            return
        time.sleep(0.005)
    pytest.fail(f"Session wait timed out: {session.status}")


def transmit(sock, **header):
    sock.sendall(encode_frame(header))


def test_pairing_config_and_scene_messages():
    with listener() as (session, sock, incoming):
        hello, payload = incoming.get(timeout=3)
        assert hello == {
            "type": "hello", "protocol": 1, "client_name": "Blender",
            "pair_token": "stored", "capabilities": ["scene_edits", "object_state",
                                                        "session_config", "mesh_instance"],
        }
        assert payload == b""
        assert not session.send({"type": "object_delete", "link_id": "mesh"})
        transmit(sock, type="pairing_pending")
        receive_until(session, lambda: session.status == "Accept in Unity")
        transmit(sock, type="hello", protocol=1, pair_token="approved",
                 capabilities=["mesh_delta_receive", "mesh_instance"])
        receive_until(session, lambda: session.pair_token == "approved")
        assert not session.ready
        assert session.capabilities == {"mesh_delta_receive", "mesh_instance"}
        assert not session.send({"type": "object_delete", "link_id": "mesh"})
        assert incoming.empty()
        transmit(sock, type="session_config", active_source="client")
        receive_until(session, lambda: session.ready)
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        full = encode_mesh_full(
            mesh_id="mesh", geometry_id="geometry", name="Triangle",
            world_matrix=np.eye(4), smooth_shading=True, positions=positions,
            corner_verts=np.array([0, 1, 2]), loop_starts=np.array([0]),
            loop_totals=np.array([3]), uv_corners_or_None=None,
            material_names=["Triangle"], face_material=np.array([0]),
        )
        changed = positions.copy()
        changed[1, 0] = 2
        delta = encode_mesh_delta(mesh_id="mesh", old_positions=positions,
                                  new_positions=changed)
        deletion = ({"type": "object_delete", "link_id": "mesh", "live_sync": True}, b"")
        for header, data in (full, delta, deletion):
            assert session.send(header, data)
            assert incoming.get(timeout=3) == (header, data)
        transmit(sock, type="mesh_ack", mesh_id="ignored")
        transmit(sock, type="ping")
        receive_until(session, lambda: not incoming.empty())
        assert incoming.get(timeout=3) == ({"type": "pong"}, b"")
        assert session.ready
        transmit(sock, type="error", message="Receiver rejected mesh")
        receive_until(session, lambda: not session.running)
        assert not session.ready
        assert session.status == "Receiver rejected mesh"
        assert not session.send(*deletion)


def test_config_gate_and_protocol_mismatch():
    with listener() as (session, sock, incoming):
        incoming.get(timeout=3)
        transmit(sock, type="session_config", active_source="client")
        transmit(sock, type="ping")
        receive_until(session, lambda: not incoming.empty())
        assert not session.ready
        incoming.get(timeout=3)
        transmit(sock, type="hello", protocol=1, capabilities=[])
        transmit(sock, type="session_config", active_source="nomad")
        receive_until(session, lambda: session.status == "Waiting for session config")
        assert not session.ready
        assert session.pair_token == "stored"
        transmit(sock, type="session_config", active_source="client")
        receive_until(session, lambda: session.ready)
        transmit(sock, type="session_config", active_source="none")
        receive_until(session, lambda: not session.ready)
        transmit(sock, type="hello", protocol=2)
        receive_until(session, lambda: not session.running)
        assert session.status == "Unsupported protocol"


@pytest.mark.parametrize("extra", [{"capabilities": "bad"}, {"pair_token": 42}])
def test_invalid_hello_stops(extra):
    with listener() as (session, sock, incoming):
        incoming.get(timeout=3)
        transmit(sock, type="hello", protocol=1, **extra)
        receive_until(session, lambda: not session.running)
        assert session.status.startswith("Invalid")

def test_connection_loss_stops():
    with listener() as (session, sock, incoming):
        incoming.get(timeout=3)
        sock.shutdown(2)
        receive_until(session, lambda: not session.running)
        assert not session.ready
        assert "disconnect" in session.status.lower()


def ready_textures(session, sock, incoming):
    incoming.get(timeout=3)
    transmit(sock, type="hello", protocol=1, capabilities=["material", "texture"])
    transmit(sock, type="session_config", active_source="client")
    receive_until(session, lambda: session.ready)


def test_texture_order_resend_clear_and_request():
    with listener() as (session, sock, incoming):
        ready_textures(session, sock, incoming)
        red = b"\x89PNG\r\n\x1a\nred"
        blue = b"\x89PNG\r\n\x1a\nblue"
        assert session.send_bakes([("mesh", 1, "Paint", {"color": red, "x_mask": blue})]) == 2
        first = [incoming.get(timeout=3) for _ in range(3)]
        assert [header["type"] for header, _ in first] == ["texture", "texture", "material"]
        ids = {header["name"]: header["texture_id"] for header, _ in first[:2]}
        material = first[2][0]
        assert material == {
            "type": "material", "mesh_id": "mesh", "slot_index": 1,
            "live_sync": False, "material": {"textures": {
                "color": {"texture_id": ids["Paint color.png"], "name": "Paint color.png"},
                "x_mask": {"texture_id": ids["Paint x_mask.png"], "name": "Paint x_mask.png"},
            }},
        }
        assert first[0][1] == red and first[1][1] == blue
        assert session.send_bakes([("mesh", 1, "Paint", {"color": red})]) == 1
        cleared, payload = incoming.get(timeout=3)
        assert payload == b""
        assert cleared["material"]["textures"]["x_mask"] == {}
        assert "texture_id" not in cleared["material"]["textures"]["x_mask"]
        transmit(sock, type="request_texture", texture_id=ids["Paint color.png"])
        receive_until(session, lambda: not incoming.empty())
        assert incoming.get(timeout=3)[1] == red
        assert session.send_bakes([("mesh", 1, "Paint", {})]) == 0
        incoming.get(timeout=3)
        assert session.send_bakes([("mesh", 1, "Paint", {"color": red})]) == 1
        resent = incoming.get(timeout=3)
        assert resent[0]["type"] == "texture" and resent[1] == red
        assert incoming.get(timeout=3)[0]["type"] == "material"


def test_texture_queue_refusal_sends_nothing():
    with listener() as (session, sock, incoming):
        ready_textures(session, sock, incoming)
        session.transport.max_queue_bytes = 100
        with pytest.raises(ValueError, match="queue limit"):
            session.send_bakes([("mesh", 0, "Paint", {"color": b"x" * 100})])
        assert incoming.empty()
        assert session._slots == {} and session._textures == {}


def test_batch_refusal_is_atomic():
    session = Session('localhost', 1)
    session.ready = session.running = True
    session.capabilities = {'material', 'texture'}
    slots = [('mesh', 0, 'Paint', {'color': b'one'})]
    messages, _, _ = _bake_messages(slots, {})
    session.transport.max_queue_bytes = len(encode_frame(*messages[0]))
    with pytest.raises(ValueError, match='queue limit'):
        session.send_bakes(slots)
    assert not session.transport._outbound
    assert session.transport._out_bytes == 0
    assert session._slots == session._textures == {}


def test_texture_references_pruned_by_mesh_changes():
    session = Session('localhost', 1)
    session.ready = session.running = True
    session.capabilities = {'material', 'texture'}
    session.send_bakes([('mesh', 0, 'A', {'color': b'a'}),
                        ('mesh', 1, 'B', {'color': b'b'})])
    assert len(session._textures) == 2
    assert session.send({'type': 'mesh_full', 'mesh_id': 'mesh', 'material_names': ['A']})
    assert len(session._slots) == len(session._textures) == 1
    assert session.send({'type': 'object_delete', 'link_id': 'mesh'})
    assert session._slots == session._textures == {}


def test_refused_texture_reply_sets_status():
    session = Session('localhost', 1)
    session._textures['id'] = ('image.png', b'data')
    session.transport.max_queue_bytes = 1
    session._receive({'type': 'request_texture', 'texture_id': 'id'})
    assert 'queue limit' in session.status


def test_unknown_texture_request_is_ignored():
    session = Session('localhost', 1)
    session._receive({'type': 'request_texture', 'texture_id': 'missing'})
    assert not session.transport._outbound
    assert session.status == 'Disconnected'


def test_stopped_transport_reports_disconnect_reason():
    session = Session('localhost', 1)
    session.ready = session.running = True
    session.capabilities = {'material', 'texture'}
    session.status = 'Listener disconnected'
    session.transport._inbound.append(({'type': 'error', 'message': 'Listener rejected texture'}, b''))
    session.transport._stop.set()
    with pytest.raises(ValueError, match='Listener rejected texture'):
        session.send_bakes([('mesh', 0, 'Paint', {'color': b'png'})])


def test_texture_move_resends_after_last_reference_clears():
    with listener() as (session, sock, incoming):
        ready_textures(session, sock, incoming)
        red, blue = b'red', b'blue'
        session.send_bakes([('a', 0, 'A', {'color': red}),
                            ('b', 0, 'B', {'color': blue})])
        for _ in range(4):
            incoming.get(timeout=3)
        session.send_bakes([('a', 0, 'A', {'color': blue}),
                            ('b', 0, 'B', {'color': red})])
        messages = [incoming.get(timeout=3) for _ in range(3)]
        assert [header['type'] for header, _ in messages] == [
            'material', 'texture', 'material']
        assert messages[1][1] == red


@pytest.mark.parametrize("label", ["", "Mask", "two words", "a-b", "é"])
def test_invalid_channel_label(label):
    with pytest.raises(ValueError, match="label"):
        channel_key(label)


def test_valid_channel_label():
    assert channel_key("mask_2") == "x_mask_2"
