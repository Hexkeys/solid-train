"""Test helper: runs the real Agent logic with a fake screen and fake input device.

usage: python test_harness.py <server-url> <id> <password>
Prints one JSON line per input action so a test can check them.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remotepad_agent as ra  # noqa: E402
from PIL import Image  # noqa: E402


def emit(**kw):
    print(json.dumps(kw), flush=True)


class FakeScreen:
    n = 0

    def open(self):
        pass

    def close(self):
        pass

    def grab(self):
        FakeScreen.n += 1
        img = Image.new("RGB", (800, 600), (30, 60, 90))
        for x in range(0, 800, 40):  # moving pattern so frames differ
            for y in range(40):
                img.putpixel(((x + FakeScreen.n * 7) % 800, y), (255, 255, 255))
        return img, (0, 0, 800, 600)


class FakeInput:
    def move(self, x, y): emit(ev="move", x=x, y=y)
    def cursor_pos(self): return (400, 300)
    def button(self, n, d): emit(ev="button", name=n, down=d)
    def scroll(self, dx, dy): emit(ev="scroll", dx=dx, dy=dy)
    def key(self, n, d): emit(ev="key", name=n, down=d)
    def type_text(self, s): emit(ev="text", s=s)
    def release_all(self): emit(ev="release_all")


if __name__ == "__main__":
    server, dev_id, pw = sys.argv[1:4]
    agent = ra.Agent(server, dev_id, pw, screen_factory=FakeScreen, input_factory=FakeInput,
                     on_status=lambda s, d="": emit(ev="status", status=s))
    agent.start()
    emit(ev="started")
    while True:
        time.sleep(1)
