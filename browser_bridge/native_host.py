"""Eva Browser Bridge -- the native-messaging host (started by the browser, not by Eva).

    browser extension  <-- native messaging (stdin/stdout) -->  this process  <-- local TCP -->  Eva

Chrome, Edge, Brave and Opera start this program when the Eva extension connects (the host
manifest registered by install.py names it and allows ONLY the Eva extension's ID). It connects to
the port Eva wrote in ~/.eva/bridge.json and proves itself with the token written there (readable
only by this Windows user). It relays messages both ways and keeps nothing.

Native messaging: each message is a 4-byte little-endian length followed by that many bytes of
UTF-8 JSON. The link to Eva: one JSON document per line. If Eva is not running it retries every
few seconds; when the browser closes the extension's port, stdin ends and this process exits.
"""
import json
import os
import socket
import struct
import sys
import threading
import time

BRIDGE_FILE = os.environ.get("EVA_BRIDGE_FILE") or os.path.join(os.path.expanduser("~"), ".eva", "bridge.json")
RETRY_SECONDS = 3.0
MAX_MESSAGE = 1024 * 1024 * 8


# ---- native messaging framing (module functions: the checks use them) ---------------------------
def read_message(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack("<I", head)
    if n > MAX_MESSAGE:
        raise ValueError(f"message of {n} bytes is too large")
    body = stream.read(n)
    if len(body) < n:
        return None
    return json.loads(body.decode("utf-8"))


def encode_message(obj) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


class Relay:
    def __init__(self, stdin, stdout, bridge_file=BRIDGE_FILE):
        self.stdin, self.stdout, self.bridge_file = stdin, stdout, bridge_file
        self.sock = None
        self.hello = None
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def to_browser(self, obj):
        with self.lock:
            self.stdout.write(encode_message(obj))
            self.stdout.flush()

    def _connect(self):
        while not self.closed.is_set():
            try:
                with open(self.bridge_file, encoding="utf-8") as f:
                    info = json.load(f)
                s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=5)
                s.settimeout(None)
                first = {"token": info["token"], "hello": self.hello or {}}
                s.sendall((json.dumps(first) + "\n").encode("utf-8"))
                self.sock = s
                return s
            except (OSError, ValueError, KeyError):
                time.sleep(RETRY_SECONDS)
        return None

    def eva_to_browser(self):
        while not self.closed.is_set():
            s = self.sock or self._connect()
            if s is None:
                return
            buf = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            self.to_browser(json.loads(line.decode("utf-8")))
            except (OSError, ValueError):
                pass
            self.sock = None                                  # Eva went away: wait for her to come back
            try:
                s.close()
            except OSError:
                pass

    def browser_to_eva(self):
        while True:
            msg = read_message(self.stdin)
            if msg is None:
                break                                         # the browser closed the port
            if msg.get("hello"):
                self.hello = msg
            s = self.sock
            if s is not None:
                try:
                    s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
                except OSError:
                    self.sock = None
        self.closed.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def run(self):
        threading.Thread(target=self.eva_to_browser, daemon=True).start()
        self.browser_to_eva()


def main():
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    Relay(sys.stdin.buffer, sys.stdout.buffer).run()


if __name__ == "__main__":
    main()
