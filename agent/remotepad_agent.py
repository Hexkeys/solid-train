"""
RemotePad agent for Windows.

Runs on the computer you want to control. It connects OUT to your RemotePad
web server (so no router/port forwarding is needed), streams the screen as
JPEG frames and performs the mouse / keyboard events sent from the web page.

Sharing only happens while this program is running. The window always shows
whether someone is connected and lets you disconnect them or quit.
"""
import ctypes
import hashlib
import io
import json
import os
import queue
import secrets
import sys
import threading
import time
from urllib.parse import urlparse

APP_NAME = "RemotePad"
APP_VERSION = "1.0.0"
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i
DEFAULT_SERVER = os.environ.get("REMOTEPAD_SERVER", "")


# ---------------------------------------------------------------- helpers
def make_dpi_aware():
    """Make screen capture and cursor coordinates use real pixels."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def config_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME, "config.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(config_path()), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def new_device_id():
    return str(100_000_000 + secrets.randbelow(900_000_000))


def new_password():
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(10))


def sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_server(url):
    """'myapp.onrender.com' / 'https://myapp.onrender.com/' -> 'wss://myapp.onrender.com/ws/agent'."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Enter your server address first.")
    if "://" not in url:
        local = url.startswith(("localhost", "127.0.0.1"))
        url = ("http://" if local else "https://") + url
    p = urlparse(url)
    scheme = {"https": "wss", "wss": "wss", "http": "ws", "ws": "ws"}.get(p.scheme)
    if not scheme or not p.netloc:
        raise ValueError("That server address does not look right.")
    return "%s://%s/ws/agent" % (scheme, p.netloc)


def fmt_id(dev_id):
    return " ".join([dev_id[i:i + 3] for i in range(0, len(dev_id), 3)])


# ------------------------------------------------------- screen backend
class MssScreen:
    """Captures the primary monitor. Create + use it inside ONE thread."""

    def __init__(self):
        self._sct = None

    def open(self):
        import mss
        self._sct = mss.mss()

    def close(self):
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

    def grab(self):
        """-> (PIL RGB image, (left, top, width, height))"""
        from PIL import Image
        mon = self._sct.monitors[1]
        shot = self._sct.grab(mon)
        img = Image.frombuffer("RGB", shot.size, shot.bgra, "raw", "BGRX", 0, 1)
        return img, (mon["left"], mon["top"], mon["width"], mon["height"])


# -------------------------------------------------------- input backend
class PynputInput:
    """Moves the real mouse and presses real keys."""

    def __init__(self):
        from pynput.keyboard import Controller as KController, Key, KeyCode
        from pynput.mouse import Controller as MController, Button
        self._Key, self._KeyCode, self._Button = Key, KeyCode, Button
        self.kb = KController()
        self.ms = MController()
        self._pressed = {}
        self._buttons = set()

        K = Key
        self._special = {
            "Enter": K.enter, "Backspace": K.backspace, "Tab": K.tab, "Escape": K.esc,
            " ": K.space, "Spacebar": K.space, "Delete": K.delete, "Insert": K.insert,
            "Home": K.home, "End": K.end, "PageUp": K.page_up, "PageDown": K.page_down,
            "ArrowUp": K.up, "ArrowDown": K.down, "ArrowLeft": K.left, "ArrowRight": K.right,
            "Shift": K.shift, "Control": K.ctrl, "Alt": K.alt, "AltGraph": K.alt_gr,
            "Meta": K.cmd, "OS": K.cmd, "CapsLock": K.caps_lock, "ContextMenu": K.menu,
            "PrintScreen": K.print_screen, "NumLock": K.num_lock, "ScrollLock": K.scroll_lock,
            "Pause": K.pause,
        }
        for n in range(1, 13):
            self._special["F%d" % n] = getattr(K, "f%d" % n)
        self._buttons_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}

    # mouse
    def move(self, x, y):
        self.ms.position = (int(x), int(y))

    def cursor_pos(self):
        return self.ms.position

    def button(self, name, down):
        b = self._buttons_map.get(name)
        if b is None:
            return
        if down:
            self.ms.press(b)
            self._buttons.add(b)
        else:
            self.ms.release(b)
            self._buttons.discard(b)

    def scroll(self, dx, dy):
        # browsers: +dy = down; pynput: +dy = up
        self.ms.scroll(int(dx), -int(dy))

    # keyboard
    def _resolve(self, name):
        if name in self._special:
            return self._special[name]
        if len(name) == 1:
            if sys.platform == "win32":
                try:
                    f = ctypes.windll.user32.VkKeyScanW
                    f.restype = ctypes.c_short
                    r = f(ord(name))
                    vk = r & 0xFF
                    if r != -1 and vk != 0xFF:
                        return self._KeyCode.from_vk(vk)
                except Exception:
                    pass
            return self._KeyCode.from_char(name)
        return None

    def key(self, name, down):
        k = self._resolve(name)
        if k is None:
            return
        if down:
            self.kb.press(k)
            self._pressed[name] = k
        else:
            self.kb.release(k)
            self._pressed.pop(name, None)

    def type_text(self, s):
        self.kb.type(s)

    def release_all(self):
        for name, k in list(self._pressed.items()):
            try:
                self.kb.release(k)
            except Exception:
                pass
        self._pressed.clear()
        for b in list(self._buttons):
            try:
                self.ms.release(b)
            except Exception:
                pass
        self._buttons.clear()


# --------------------------------------------------------------- the agent
class Agent:
    """Connection + capture + input logic (no GUI)."""

    def __init__(self, server_url, dev_id, password, screen_factory=MssScreen,
                 input_factory=PynputInput, on_status=None, log=None):
        self.ws_url = normalize_server(server_url)
        self.dev_id = dev_id
        self.password = password
        self.screen_factory = screen_factory
        self.input_factory = input_factory
        self.on_status = on_status or (lambda status, detail="": None)
        self.log = log or (lambda *a: None)

        self.quality = 55
        self.max_width = 1280
        self.fps = 10

        self.ws = None
        self.viewer_on = False
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._thread = None
        self._cap_thread = None
        self._cap_gen = 0
        self._input = None
        self.geo = None  # (left, top, w, h) of the captured monitor

    # ---- lifecycle
    def start(self):
        self._thread = threading.Thread(target=self._run, name="agent-conn", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.viewer_on = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self._input:
            try:
                self._input.release_all()
            except Exception:
                pass

    def set_password(self, password):
        self.password = password
        self._send_json({"t": "setpw", "pw": sha256_hex(password)})

    def kick_viewer(self):
        self._send_json({"t": "kick"})

    # ---- connection loop
    def _run(self):
        import websocket
        delay = 1.0
        while not self._stop.is_set():
            self.on_status("connecting", "")
            got_hello = {"v": False}

            def on_open(ws):
                ws.send(json.dumps({"t": "hello", "id": self.dev_id, "pw": sha256_hex(self.password)}))

            def on_message(ws, msg):
                if isinstance(msg, (bytes, bytearray)):
                    return
                try:
                    m = json.loads(msg)
                except Exception:
                    return
                if m.get("t") == "hello_ok":
                    got_hello["v"] = True
                    self.on_status("online", "")
                else:
                    self._on_message(m)

            def on_close(ws, code, reason):
                self._viewer_off()

            self.ws = websocket.WebSocketApp(self.ws_url, on_open=on_open,
                                             on_message=on_message, on_close=on_close)
            try:
                self.ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:  # network errors, DNS, TLS...
                self.log("connection error:", e)
            self._viewer_off()
            if self._stop.is_set():
                break
            if got_hello["v"]:
                delay = 1.0
            self.on_status("offline", "Reconnecting…")
            self._stop.wait(delay)
            delay = min(delay * 2, 20.0)

    def _on_message(self, m):
        t = m.get("t")
        if t == "viewer":
            if m.get("on"):
                self._viewer_on()
            else:
                self._viewer_off()
            return
        if t == "reject":
            self.on_status("error", "This ID is already used by another computer. Restart the app.")
            return
        if not self.viewer_on:
            return
        try:
            self.handle_input(m)
        except Exception as e:
            self.log("input error:", e)

    # ---- viewer presence
    def _viewer_on(self):
        if self.viewer_on:
            return
        self.viewer_on = True
        self.on_status("viewer", "")
        self._cap_gen += 1
        gen = self._cap_gen
        self._cap_thread = threading.Thread(target=self._capture_loop, args=(gen,), daemon=True)
        self._cap_thread.start()

    def _viewer_off(self):
        was = self.viewer_on
        self.viewer_on = False
        self._cap_gen += 1
        if self._input:
            try:
                self._input.release_all()
            except Exception:
                pass
        if was and not self._stop.is_set():
            self.on_status("online", "")

    # ---- sending
    def _send_json(self, obj):
        try:
            with self._send_lock:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.ws.send(json.dumps(obj))
        except Exception:
            pass

    def _send_binary(self, data):
        try:
            import websocket
            with self._send_lock:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception:
            pass

    # ---- capture
    def _capture_loop(self, gen):
        screen = self.screen_factory()
        try:
            screen.open()
        except Exception as e:
            self.log("capture init failed:", e)
            self.on_status("error", "Screen capture failed: %s" % e)
            return
        last_digest = None
        last_size = None
        last_cur = None
        last_frame_t = 0.0
        try:
            while self.viewer_on and gen == self._cap_gen and not self._stop.is_set():
                t0 = time.time()
                img, geo = screen.grab()
                self.geo = geo
                if (geo[2], geo[3]) != last_size:
                    last_size = (geo[2], geo[3])
                    self._send_json({"t": "size", "w": geo[2], "h": geo[3]})
                    last_digest = None
                if img.width > self.max_width:
                    h = max(1, int(img.height * self.max_width / img.width))
                    img = img.resize((self.max_width, h))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=int(self.quality))
                data = buf.getvalue()
                digest = hashlib.md5(data).digest()
                if digest != last_digest or t0 - last_frame_t > 2.0:
                    self._send_binary(data)
                    last_digest = digest
                    last_frame_t = t0
                if self._input:
                    try:
                        cx, cy = self._input.cursor_pos()
                        nx = round(min(max((cx - geo[0]) / geo[2], 0.0), 1.0), 4)
                        ny = round(min(max((cy - geo[1]) / geo[3], 0.0), 1.0), 4)
                        if (nx, ny) != last_cur:
                            last_cur = (nx, ny)
                            self._send_json({"t": "cur", "x": nx, "y": ny})
                    except Exception:
                        pass
                wait = (1.0 / max(1, self.fps)) - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)
        except Exception as e:
            self.log("capture error:", e)
        finally:
            screen.close()

    # ---- input
    def _ensure_input(self):
        if self._input is None:
            self._input = self.input_factory()
        return self._input

    def _abs(self, x, y):
        if not self.geo:
            return None
        x = min(max(float(x), 0.0), 1.0)
        y = min(max(float(y), 0.0), 1.0)
        left, top, w, h = self.geo
        return left + int(round(x * (w - 1))), top + int(round(y * (h - 1)))

    def handle_input(self, m):
        inp = self._ensure_input()
        t = m.get("t")
        if t == "mv":
            p = self._abs(m.get("x", 0), m.get("y", 0))
            if p:
                inp.move(*p)
        elif t == "bt":
            if "x" in m and "y" in m:
                p = self._abs(m["x"], m["y"])
                if p:
                    inp.move(*p)
            inp.button(str(m.get("b", "left")), bool(m.get("d")))
        elif t == "wh":
            dx = max(-20, min(20, int(m.get("dx", 0))))
            dy = max(-20, min(20, int(m.get("dy", 0))))
            inp.scroll(dx, dy)
        elif t in ("kd", "ku"):
            k = str(m.get("k", ""))
            if 0 < len(k) <= 20:
                inp.key(k, t == "kd")
        elif t == "tx":
            s = str(m.get("s", ""))[:200]
            if s:
                inp.type_text(s)
        elif t == "ra":
            inp.release_all()
        elif t == "q":
            self.quality = max(20, min(90, int(m.get("quality", self.quality))))
            self.max_width = max(480, min(3840, int(m.get("width", self.max_width))))
            self.fps = max(1, min(20, int(m.get("fps", self.fps))))


# ------------------------------------------------------------------- GUI
def run_gui():
    import tkinter as tk
    from tkinter import messagebox

    make_dpi_aware()
    cfg = load_config()
    if not cfg.get("id"):
        cfg["id"] = new_device_id()
    if not cfg.get("password"):
        cfg["password"] = new_password()
    if not cfg.get("server") and DEFAULT_SERVER:
        cfg["server"] = DEFAULT_SERVER
    save_config(cfg)

    PINK, TEAL, INK = "#d27ba0", "#7dd6c8", "#111111"
    root = tk.Tk()
    root.title("%s Agent" % APP_NAME)
    root.configure(bg="white")
    root.resizable(False, False)

    state = {"agent": None}
    status_var = tk.StringVar(value="Not started")
    detail_var = tk.StringVar(value="")
    pw_var = tk.StringVar(value=cfg["password"])
    server_var = tk.StringVar(value=cfg.get("server", ""))

    head = tk.Frame(root, bg=PINK, highlightbackground=INK, highlightthickness=2)
    head.pack(fill="x", padx=14, pady=(14, 8))
    tk.Label(head, text="RemotePad", bg=PINK, fg="white", font=("Segoe UI", 16, "bold")).pack(padx=12, pady=(8, 0))
    tk.Label(head, text="Share this computer", bg=PINK, fg="white", font=("Segoe UI", 9)).pack(padx=12, pady=(0, 8))

    body = tk.Frame(root, bg="white")
    body.pack(fill="x", padx=18)

    def row(label, var, copy=True, show=None):
        tk.Label(body, text=label, bg="white", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(8, 0))
        fr = tk.Frame(body, bg="white")
        fr.pack(fill="x")
        e = tk.Entry(fr, textvariable=var, font=("Consolas", 15), relief="solid", bd=1,
                     state="readonly", readonlybackground=TEAL if copy else "white", width=17)
        e.pack(side="left", ipady=4)
        if copy:
            def do_copy():
                root.clipboard_clear()
                root.clipboard_append(var.get().replace(" ", ""))
            tk.Button(fr, text="Copy", command=do_copy, relief="solid", bd=1, bg="white").pack(side="left", padx=6)
        return fr

    id_var = tk.StringVar(value=fmt_id(cfg["id"]))
    row("Computer ID", id_var)
    pw_row = row("Password", pw_var)

    def regen_pw():
        pw = new_password()
        cfg["password"] = pw
        save_config(cfg)
        pw_var.set(pw)
        if state["agent"]:
            state["agent"].set_password(pw)

    tk.Button(pw_row, text="New", command=regen_pw, relief="solid", bd=1, bg="white").pack(side="left")

    tk.Label(body, text="Server address (your Render URL)", bg="white", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(12, 0))
    srv_entry = tk.Entry(body, textvariable=server_var, font=("Segoe UI", 11), relief="solid", bd=1)
    srv_entry.pack(fill="x", ipady=4)

    status_lbl = tk.Label(body, textvariable=status_var, bg="white", font=("Segoe UI", 11, "bold"), anchor="w")
    status_lbl.pack(fill="x", pady=(14, 0))
    tk.Label(body, textvariable=detail_var, bg="white", fg="#666", font=("Segoe UI", 9), anchor="w",
             wraplength=300, justify="left").pack(fill="x")

    btns = tk.Frame(root, bg="white")
    btns.pack(fill="x", padx=18, pady=(10, 16))
    start_btn = tk.Button(btns, text="Start sharing", bg=PINK, fg="white", relief="solid", bd=1,
                          font=("Segoe UI", 10, "bold"), padx=12, pady=4)
    start_btn.pack(side="left")
    kick_btn = tk.Button(btns, text="Disconnect viewer", relief="solid", bd=1, bg="white", padx=8, pady=4,
                         state="disabled", command=lambda: state["agent"] and state["agent"].kick_viewer())
    kick_btn.pack(side="left", padx=8)

    colors = {"online": "#1f8f55", "viewer": "#c0392b", "connecting": "#b58900", "offline": "#b58900", "error": "#c0392b"}
    texts = {"online": "Online - waiting for you to connect", "viewer": "Someone is controlling this computer",
             "connecting": "Connecting…", "offline": "Offline", "error": "Problem"}

    updates = queue.Queue()

    def on_status(status, detail=""):  # called from background threads
        updates.put((status, detail))

    def poll_updates():  # runs in the Tk main thread
        try:
            while True:
                status, detail = updates.get_nowait()
                if not state["agent"]:
                    continue  # sharing was stopped meanwhile
                status_var.set(texts.get(status, status))
                status_lbl.configure(fg=colors.get(status, "black"))
                detail_var.set(detail)
                kick_btn.configure(state="normal" if status == "viewer" else "disabled")
                root.title("%s Agent%s" % (APP_NAME, " - IN USE" if status == "viewer" else ""))
        except queue.Empty:
            pass
        root.after(150, poll_updates)

    def start():
        if state["agent"]:
            return
        try:
            normalize_server(server_var.get())
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        cfg["server"] = server_var.get().strip()
        save_config(cfg)
        a = Agent(cfg["server"], cfg["id"], cfg["password"], on_status=on_status)
        state["agent"] = a
        a.start()
        start_btn.configure(text="Stop sharing", command=stop)
        srv_entry.configure(state="disabled")

    def stop():
        a = state["agent"]
        if a:
            a.stop()
        state["agent"] = None
        start_btn.configure(text="Start sharing", command=start)
        srv_entry.configure(state="normal")
        status_var.set("Not sharing")
        status_lbl.configure(fg="black")
        detail_var.set("")
        kick_btn.configure(state="disabled")
        root.title("%s Agent" % APP_NAME)

    start_btn.configure(command=start)

    def on_close():
        stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll_updates()
    if cfg.get("server"):
        root.after(300, start)  # auto-start when a server is already saved
    root.mainloop()


def run_headless(server, dev_id=None, password=None):
    """Console mode:  remotepad_agent.py --headless https://myapp.onrender.com"""
    make_dpi_aware()
    cfg = load_config()
    dev_id = dev_id or cfg.get("id") or new_device_id()
    password = password or cfg.get("password") or new_password()
    cfg.update({"id": dev_id, "password": password, "server": server})
    save_config(cfg)
    print("Computer ID:", fmt_id(dev_id))
    print("Password   :", password)
    a = Agent(server, dev_id, password, on_status=lambda s, d="": print("[status]", s, d), log=print)
    a.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        a.stop()


if __name__ == "__main__":
    if "--headless" in sys.argv:
        i = sys.argv.index("--headless")
        srv = sys.argv[i + 1] if len(sys.argv) > i + 1 else DEFAULT_SERVER
        run_headless(srv)
    else:
        run_gui()
