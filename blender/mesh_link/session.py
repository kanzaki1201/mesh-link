import hashlib
import re
from collections import Counter

from .transport import Transport


def channel_key(name):
    name = re.sub(r"[ -]+", "_", name.strip().lower())
    if not name:
        raise ValueError("type a channel name on the Mesh Link Channel node")
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError("use letters, digits, spaces, hyphens, or underscores "
                         "(letters are lowercased)")
    return "x_" + name


def _slot_textures(material_name, channels):
    textures, references, source = {}, {}, {}
    for channel, data in channels.items():
        texture_id = hashlib.sha256(data).hexdigest()
        name = f"{material_name} {channel}.png"
        references[channel] = (texture_id, name)
        textures[channel] = {"texture_id": texture_id, "name": name}
        source[texture_id] = (name, data)
    return textures, references, source


def _bake_messages(slots, old_slots, auto=False):
    messages = []
    new_slots = dict(old_slots)
    blobs = {}
    counts = Counter(texture_id for references in old_slots.values()
                     for texture_id, _ in references.values())
    for slot in slots:
        mesh_id, slot_index, material_name, channels = slot[:4]
        current = set(slot[4]) if len(slot) == 5 else set(channels)
        key = (mesh_id, slot_index)
        previous = old_slots.get(key, {})
        textures, references, source = _slot_textures(material_name, channels)
        for channel in previous.keys() - current:
            textures[channel] = {}
        references = {**previous, **references}
        for channel in previous.keys() - current:
            del references[channel]
        for texture_id, (name, data) in source.items():
            if counts[texture_id] == 0:
                blobs[texture_id] = (name, data)
                messages.append(({
                    "type": "texture", "texture_id": texture_id,
                    "name": name, "binary_size": len(data),
                }, data))
        new_slots[key] = references
        if textures or not auto:
            messages.append(({
                "type": "material", "mesh_id": mesh_id, "slot_index": slot_index,
                "live_sync": False, "material": {"textures": textures},
            }, b""))
        counts.subtract(texture_id for texture_id, _ in previous.values())
        counts.update(texture_id for texture_id, _ in references.values())
    return messages, new_slots, blobs


class Session:
    def __init__(self, host, port, pair_token=""):
        self.transport = Transport(host, port)
        self.pair_token = pair_token
        self.capabilities = set()
        self.ready = False
        self.running = False
        self.status = "Disconnected"
        self._hello = False
        self._slots = {}
        self._textures = {}

    def connect(self):
        self.running = True
        self.status = "Connecting"
        self.transport.send({
            "type": "hello", "protocol": 1, "client_name": "Blender",
            "pair_token": self.pair_token,
            "capabilities": ["scene_edits", "object_state", "session_config",
                             "mesh_instance"],
        })
        self.transport.start()

    def close(self, message="Disconnected"):
        self.ready = False
        self.running = False
        self.status = message
        self.transport.close()
        self._slots.clear()
        self._textures.clear()

    def send(self, header, payload=b""):
        if not self.ready or not self.running or not self.transport.send(header, payload):
            return False
        if header["type"] == "object_delete":
            mesh_id = header["link_id"]
            self._slots = {key: value for key, value in self._slots.items()
                           if key[0] != mesh_id}
            self._keep_referenced_textures()
        elif header["type"] == "mesh_full":
            mesh_id = header["mesh_id"]
            count = len(header.get("material_names", ()))
            self._slots = {key: value for key, value in self._slots.items()
                           if key[0] != mesh_id or key[1] < count}
            self._keep_referenced_textures()
        return True

    def _keep_referenced_textures(self):
        self._textures = {texture_id: self._textures[texture_id]
                          for references in self._slots.values()
                          for texture_id, _ in references.values()}

    def send_bakes(self, slots, auto=False):
        if self.transport._stop.is_set():
            self.drain()
            raise ValueError(self.status if self.status != 'Connected' else 'Listener disconnected')
        if not self.ready or not self.running or not {'material', 'texture'} <= self.capabilities:
            raise ValueError("Listener does not support material and texture")
        messages, new_slots, blobs = _bake_messages(slots, self._slots, auto)
        if not self.transport.send_batch(messages):
            if self.transport._stop.is_set():
                self.drain()
                raise ValueError(self.status if self.status != 'Connected' else 'Listener disconnected')
            raise ValueError("Texture messages exceed send queue limit")
        self._slots = new_slots
        available = self._textures | blobs
        self._textures = {texture_id: available[texture_id]
                          for references in new_slots.values()
                          for texture_id, _ in references.values()}
        return sum(len(slot[3]) for slot in slots)

    def drain(self):
        for header, _payload in self.transport.poll():
            if not self.running:
                break
            self._receive(header)

    def _receive(self, header):
        kind = header["type"]
        if kind == "error":
            self.close(str(header.get("message", "Link error")))
        elif kind == "ping":
            self.transport.send({"type": "pong"})
        elif kind == "pairing_pending":
            self.status = "Accept in Unity"
        elif kind == "hello":
            self._accept_hello(header)
        elif kind == "session_config" and self._hello:
            self.ready = header.get("active_source") == "client"
            self.status = "Connected" if self.ready else "Waiting for session config"
        elif kind == "request_texture":
            texture_id = header.get("texture_id")
            blob = self._textures.get(texture_id)
            if blob is not None:
                name, data = blob
                try:
                    sent = self.transport.send({"type": "texture", "texture_id": texture_id,
                                                "name": name, "binary_size": len(data)}, data)
                except ValueError:
                    sent = False
                if not sent:
                    self.status = "Texture reply exceeds send queue limit"

    def _accept_hello(self, header):
        if header.get("protocol") != 1:
            self.close("Unsupported protocol")
            return
        capabilities = header.get("capabilities", [])
        token = header.get("pair_token", self.pair_token)
        if not isinstance(capabilities, list) or not all(
                isinstance(value, str) for value in capabilities):
            self.close("Invalid hello capabilities")
            return
        if not isinstance(token, str):
            self.close("Invalid pair token")
            return
        self.capabilities = set(capabilities)
        self.pair_token = token
        self._hello = True
        self.ready = False
        self.status = "Waiting for session config"
