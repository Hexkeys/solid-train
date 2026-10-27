"""Unit tests for the agent logic that need no network, display or Windows.

Run:  python test_agent_core.py
(needs only Pillow; the websocket module is stubbed)
"""
import json
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# stub the websocket-client module so the agent can be imported/tested anywhere
stub = types.ModuleType("websocket")
stub.ABNF = types.SimpleNamespace(OPCODE_BINARY=2)
sys.modules.setdefault("websocket", stub)

import remotepad_agent as ra  # noqa: E402
from PIL import Image  # noqa: E402


class FakeSock:
    connected = True


class FakeWS:
    def __init__(self):
        self.sock = FakeSock()
        self.texts, self.bins = [], []

    def send(self, data, opcode=None):
        (self.bins if opcode == 2 else self.texts).append(data)

    def close(self):
        pass


class FakeScreen:
    def open(self): pass
    def close(self): pass
    def grab(self):
        return Image.new("RGB", (1920, 1080), (10, 120, 200)), (100, 50, 1920, 1080)


class FakeInput:
    def __init__(self): self.calls = []
    def move(self, x, y): self.calls.append(("move", x, y))
    def cursor_pos(self): return (1060, 590)
    def button(self, n, d): self.calls.append(("button", n, d))
    def scroll(self, dx, dy): self.calls.append(("scroll", dx, dy))
    def key(self, n, d): self.calls.append(("key", n, d))
    def type_text(self, s): self.calls.append(("text", s))
    def release_all(self): self.calls.append(("release_all",))


def make_agent():
    a = ra.Agent("https://example.onrender.com", "123456789", "pw", screen_factory=FakeScreen,
                 input_factory=FakeInput)
    a.ws = FakeWS()
    return a


class Tests(unittest.TestCase):
    def test_server_url(self):
        n = ra.normalize_server
        self.assertEqual(n("myapp.onrender.com"), "wss://myapp.onrender.com/ws/agent")
        self.assertEqual(n("https://myapp.onrender.com/"), "wss://myapp.onrender.com/ws/agent")
        self.assertEqual(n("http://localhost:3000"), "ws://localhost:3000/ws/agent")
        self.assertEqual(n("localhost:3000"), "ws://localhost:3000/ws/agent")
        with self.assertRaises(ValueError):
            n("   ")

    def test_id_and_password(self):
        for _ in range(50):
            self.assertRegex(ra.new_device_id(), r"^[1-9][0-9]{8}$")
            self.assertEqual(len(ra.new_password()), 10)
        self.assertEqual(ra.fmt_id("123456789"), "123 456 789")
        self.assertEqual(len(ra.sha256_hex("x")), 64)

    def test_input_mapping(self):
        a = make_agent()
        a.geo = (100, 50, 1920, 1080)
        a.viewer_on = True
        inp = a._ensure_input()
        a.handle_input({"t": "mv", "x": 0, "y": 0})
        a.handle_input({"t": "mv", "x": 1, "y": 1})
        a.handle_input({"t": "mv", "x": 5, "y": -3})  # clamped
        self.assertEqual(inp.calls[0], ("move", 100, 50))
        self.assertEqual(inp.calls[1], ("move", 100 + 1919, 50 + 1079))
        self.assertEqual(inp.calls[2], ("move", 100 + 1919, 50))
        inp.calls.clear()
        a.handle_input({"t": "bt", "b": "left", "d": True, "x": 0.5, "y": 0.5})
        a.handle_input({"t": "bt", "b": "left", "d": False})
        a.handle_input({"t": "wh", "dx": 0, "dy": 99})  # clamped to 20
        a.handle_input({"t": "kd", "k": "Control"})
        a.handle_input({"t": "kd", "k": "c"})
        a.handle_input({"t": "ku", "k": "c"})
        a.handle_input({"t": "ku", "k": "Control"})
        a.handle_input({"t": "kd", "k": "x" * 50})  # too long, ignored
        a.handle_input({"t": "tx", "s": "สวัสดี hello"})
        a.handle_input({"t": "ra"})
        self.assertEqual(inp.calls, [
            ("move", 100 + 960, 50 + 540),
            ("button", "left", True),
            ("button", "left", False),
            ("scroll", 0, 20),
            ("key", "Control", True), ("key", "c", True), ("key", "c", False), ("key", "Control", False),
            ("text", "สวัสดี hello"),
            ("release_all",),
        ])

    def test_quality_settings_clamped(self):
        a = make_agent()
        a.viewer_on = True
        a.handle_input({"t": "q", "quality": 999, "width": 10, "fps": 500})
        self.assertEqual((a.quality, a.max_width, a.fps), (90, 480, 20))

    def test_input_ignored_without_viewer(self):
        a = make_agent()
        a.geo = (0, 0, 100, 100)
        a._on_message({"t": "mv", "x": 0.5, "y": 0.5})
        self.assertIsNone(a._input)  # never even created

    def test_capture_streams_jpeg_and_cursor(self):
        a = make_agent()
        a.fps = 20
        a._input = FakeInput()
        a._viewer_on()
        time.sleep(0.6)
        a._viewer_off()
        time.sleep(0.15)
        self.assertGreaterEqual(len(a.ws.bins), 1)
        self.assertTrue(a.ws.bins[0][:2] == b"\xff\xd8", "frame is a JPEG")
        img = Image.open(__import__("io").BytesIO(a.ws.bins[0]))
        self.assertEqual(img.size, (1280, 720), "scaled to max width, aspect kept")
        msgs = [json.loads(t) for t in a.ws.texts]
        self.assertIn({"t": "size", "w": 1920, "h": 1080}, msgs)
        cur = [m for m in msgs if m["t"] == "cur"]
        self.assertEqual(cur[0], {"t": "cur", "x": 0.5, "y": 0.5})
        # identical screen => unchanged frames are not re-sent every tick
        self.assertLess(len(a.ws.bins), 8)
        self.assertIn(("release_all",), a._input.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
