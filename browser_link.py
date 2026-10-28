"""Eva -- working in the person's OWN browser through the Eva Browser Bridge extension.

Release plan, Phase 2 item 1 ("the way UiPath and Automation Anywhere do it"): an extension in
Chrome / Edge / Brave / Opera reads the page they have open -- its real elements and their labels --
and clicks and types there, in their signed-in session. Before this, Eva could only open a URL in
their browser (navigate_browser) and had to read or work on pages in her own automation browser
or by looking at the screen.

    browser_bridge/extension/   the extension (Manifest V3); fixed ID, see EXTENSION_ID
    browser_bridge/native_host.py   started by the browser; relays to Eva over local TCP
    browser_link.py (this)      Eva's end: a server on 127.0.0.1 (random port + token in
                                ~/.eva/bridge.json), and the page_* tools

Safety, the same as everywhere else:
  * every click is judged by the element's own label and the page (finance_guard for money,
    system_guard for "Delete account", "Remove", "Sign out"...), read from the page -- never from
    what the model calls it -- and asks before anything spends money or cannot be undone;
  * typing: payment fields and card numbers ask; password fields are refused outright;
  * page text is third-party text: the tools are web=True, so the turn is tainted.
"""
import hmac
import json
import os
import secrets
import socket
import threading
import time

import finance_guard
import system_guard
from logger import get_logger

log = get_logger(__name__)

EXTENSION_ID = "dcnonlenmnakmjokkofhknaodcapnfnd"
HOST_NAME = "com.eva.browser_bridge"
BRIDGE_FILE = os.path.join(os.path.expanduser("~"), ".eva", "bridge.json")
CALL_TIMEOUT = 20.0
NOT_CONNECTED = ("the Eva browser extension is not connected (no browser has it running). Use navigate_browser "
                 "to open a page in their browser, or read_web_page / browser_open instead. To connect it: "
                 "install_browser_bridge.bat, then load browser_bridge\\extension in the browser (one time).")


class BridgeError(Exception):
    pass


class _Conn:
    def __init__(self, sock, name):
        self.sock, self.name = sock, name
        self.pending = {}                 # id -> [Event, reply]
        self.lock = threading.Lock()
        self.last = time.time()
        self.alive = True

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            self.sock.sendall(data)


class BridgeServer:
    def __init__(self, bridge_file=BRIDGE_FILE, host="127.0.0.1"):
        self.bridge_file, self.host = bridge_file, host
        self.token = secrets.token_hex(24)
        self.conns = []
        self._lock = threading.Lock()
        self._ids = 0
        self._listener = None
        self.port = None

    # ---- life ---------------------------------------------------------------------------------
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((self.host, 0))                       # 127.0.0.1 only: nothing off this PC can connect
        s.listen(4)
        self._listener, self.port = s, s.getsockname()[1]
        os.makedirs(os.path.dirname(self.bridge_file), exist_ok=True)
        tmp = self.bridge_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
        os.replace(tmp, self.bridge_file)
        threading.Thread(target=self._accept, daemon=True, name="eva-browser-bridge").start()
        log.info(f"Browser bridge listening on 127.0.0.1:{self.port}")
        return self

    def stop(self):
        try:
            if self._listener:
                self._listener.close()
        except OSError:
            pass
        for c in list(self.conns):
            self._drop(c)
        try:
            with open(self.bridge_file, encoding="utf-8") as f:
                ours = json.load(f).get("token") == self.token
            if ours:                       # closed first: Windows cannot delete an open file (14:58 checks)
                os.remove(self.bridge_file)
        except (OSError, ValueError):
            pass

    def _accept(self):
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        f = sock.makefile("rb")
        try:
            sock.settimeout(10)
            first = json.loads(f.readline().decode("utf-8") or "{}")
            sock.settimeout(None)
        except (OSError, ValueError):
            sock.close()
            return
        if not hmac.compare_digest(str(first.get("token", "")), self.token):
            log.warning("Browser bridge: a connection without the right token was refused")
            sock.close()
            return
        conn = _Conn(sock, (first.get("hello") or {}).get("browser") or "browser")
        with self._lock:
            self.conns.append(conn)
        log.info(f"Browser bridge: {conn.name} connected")
        try:
            for raw in f:
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                conn.last = time.time()
                if msg.get("hello"):
                    conn.name = msg.get("browser") or conn.name
                    continue
                waiter = conn.pending.pop(msg.get("id"), None)
                if waiter:
                    waiter[1] = msg
                    waiter[0].set()
        except OSError:
            pass
        self._drop(conn)
        log.info(f"Browser bridge: {conn.name} disconnected")

    def _drop(self, conn):
        conn.alive = False
        with self._lock:
            if conn in self.conns:
                self.conns.remove(conn)
        for ev, _ in list(conn.pending.values()):
            ev.set()
        try:
            conn.sock.close()
        except OSError:
            pass

    # ---- use ----------------------------------------------------------------------------------
    def browsers(self):
        with self._lock:
            return [c.name for c in self.conns if c.alive]

    def _pick(self, browser=None):
        with self._lock:
            live = [c for c in self.conns if c.alive]
        if not live:
            raise BridgeError(NOT_CONNECTED)
        if browser:
            named = [c for c in live if c.name == str(browser).lower()]
            if not named:
                raise BridgeError(f"{browser} does not have the Eva extension running; connected: "
                                  f"{', '.join(c.name for c in live)}")
            return named[0]
        return max(live, key=lambda c: c.last)            # the one used most recently

    def call(self, cmd, args=None, browser=None, timeout=CALL_TIMEOUT):
        """Send one request to a browser and wait for its answer (the result, or BridgeError)."""
        conn = self._pick(browser)
        with self._lock:
            self._ids += 1
            rid = self._ids
        waiter = [threading.Event(), None]
        conn.pending[rid] = waiter
        try:
            conn.send({"id": rid, "cmd": cmd, "args": args or {}})
        except OSError as e:
            conn.pending.pop(rid, None)
            self._drop(conn)
            raise BridgeError(f"the browser link dropped: {e}")
        if not waiter[0].wait(timeout):
            conn.pending.pop(rid, None)
            raise BridgeError(f"{conn.name} did not answer within {timeout:.0f} s")
        reply = waiter[1]
        if reply is None:
            raise BridgeError("the browser link closed before answering")
        if not reply.get("ok"):
            raise BridgeError(str(reply.get("error") or "the browser refused"))
        return reply.get("result")


SERVER = {"s": None}
approval_hook = None          # set by the app: request -> verdict (the same dialog as every approval)


def start():
    if SERVER["s"] is None:
        try:
            SERVER["s"] = BridgeServer().start()
        except OSError as e:
            log.warning(f"Browser bridge could not start: {e}")
    return SERVER["s"]


def stop():
    if SERVER["s"] is not None:
        SERVER["s"].stop()
        SERVER["s"] = None


def _server():
    s = SERVER["s"]
    if s is None:
        raise BridgeError(NOT_CONNECTED)
    return s


def _guarded(fn):
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except BridgeError as e:
            return {"ok": False, "error": str(e)}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _ask(kind, title, detail, reason):
    import permissions
    req = permissions.ApprovalRequest("page_" + kind, "FINANCIAL" if title.startswith("This involves money") else
                                      "DESTROY", title, detail, f"Because {reason}.", False, "page_" + kind)
    hook = approval_hook
    if hook is None:
        try:
            import browser_agent
            hook = browser_agent.approval_hook
        except Exception:
            hook = None
    if hook is None:
        log.warning("A browser step needs approval but nobody can be asked -- refused")
        return False
    verdict = hook(req)
    return bool(getattr(verdict, "allowed", verdict))


def _where(desc):
    from urllib.parse import urlparse
    u = urlparse(desc.get("url") or "")
    return f"{u.netloc}{u.path[:60]}"


# ---- the tools --------------------------------------------------------------------------------
@_guarded
def page_tabs(browser=None):
    """The tabs open in the person's browser."""
    s = _server()
    tabs = s.call("tabs", {}, browser)
    return {"ok": True, "browser": s._pick(browser).name, "browsers": s.browsers(), "tabs": tabs}


@_guarded
def page_activate(tab_id, browser=None):
    return {"ok": True, **_server().call("activate", {"tab_id": int(tab_id)}, browser)}


@_guarded
def page_go(url, new_tab=True, browser=None):
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return {"ok": True, **_server().call("go", {"url": url, "new_tab": bool(new_tab)}, browser, timeout=30)}


@_guarded
def page_elements(tab_id=None, contains=None, browser=None):
    """The page's clickable and typeable elements with their real labels: in view first."""
    r = _server().call("elements", {"tab_id": tab_id, "contains": contains or "", "max": 150}, browser)
    for e in r.get("elements", []):
        e.pop("attrs", None)
    hidden = r.get("total", 0) - len(r.get("elements", []))
    if hidden > 0:
        r["note"] = f"{hidden} more elements not listed; pass contains='words' to find one by its label."
    return {"ok": True, **r}


@_guarded
def page_read(tab_id=None, browser=None):
    """The page's text (what the person sees), title and address."""
    return {"ok": True, **_server().call("read", {"tab_id": tab_id, "max_chars": 12000}, browser)}


@_guarded
def page_scroll(direction="down", tab_id=None, browser=None):
    return {"ok": True, **_server().call("scroll", {"tab_id": tab_id, "direction": direction}, browser)}


@_guarded
def page_click(element_id, tab_id=None, browser=None):
    """Click an element from page_elements -- judged by its own label first."""
    s = _server()
    desc = s.call("describe", {"tab_id": tab_id, "element_id": element_id}, browser)
    label = desc.get("name") or ""
    money = finance_guard.browser_step_reason("click", {"text": label, "type": desc.get("type")},
                                              desc.get("url", ""), desc.get("title", ""))
    harm = None if money else system_guard.control_reason(label)
    if money or harm:
        title = "This involves money" if money else "This can't be undone"
        detail = f"Click “{label[:60]}”\non {_where(desc)}"
        if not _ask("click", title, detail, money or harm):
            import permissions
            return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    r = s.call("click", {"tab_id": desc.get("tab_id", tab_id), "element_id": element_id}, browser)
    time.sleep(0.6)
    return {"ok": True, **r, "note": "list the elements or read the page to see what the click did"}


@_guarded
def page_type(element_id, text, submit=False, mode="replace", tab_id=None, browser=None):
    """Type into a field from page_elements (never a password field)."""
    s = _server()
    desc = s.call("describe", {"tab_id": tab_id, "element_id": element_id}, browser)
    if desc.get("password"):
        return {"ok": False, "error": "that is a password field: Eva never types passwords -- the person types it"}
    reason = finance_guard.browser_step_reason("type", {"attrs": desc.get("attrs", "")}, desc.get("url", ""),
                                               desc.get("title", ""), typed=str(text))
    if not reason and submit:
        reason = finance_guard.browser_step_reason("press", {}, desc.get("url", ""), desc.get("title", ""), "enter")
    if reason:
        detail = f"Type into “{(desc.get('name') or 'a field')[:60]}”\non {_where(desc)}"
        if not _ask("type", "This involves money", detail, reason):
            import permissions
            return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    r = s.call("type", {"tab_id": desc.get("tab_id", tab_id), "element_id": element_id, "text": str(text),
                        "submit": bool(submit), "mode": mode}, browser)
    return {"ok": True, **r}


def status():
    s = SERVER["s"]
    return {"listening": s is not None, "browsers": s.browsers() if s else [], "extension_id": EXTENSION_ID}
