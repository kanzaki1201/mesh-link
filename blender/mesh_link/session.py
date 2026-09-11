from .transport import Transport


class Session:
    def __init__(self, host, port, pair_token=""):
        self.transport = Transport(host, port)
        self.pair_token = pair_token
        self.capabilities = set()
        self.ready = False
        self.running = False
        self.status = "Disconnected"
        self._hello = False

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

    def send(self, header, payload=b""):
        return self.ready and self.running and self.transport.send(header, payload)

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
