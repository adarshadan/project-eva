"""Eva -- the conversation, kept so it can be read back in the chat window.

Every message -- what the person said or typed, what Eva answered (long
answers in full) -- is appended to ~/.eva/chat-history.jsonl, so the chat
window shows the conversation even after a restart. A finished task's card
(core/task_card.py) is kept in place among the messages (role "card"). The file is trimmed to
the newest MAX_KEEP messages. Qt-free (tested by run_checks).
"""
import json
import os
import threading
import time

MAX_KEEP = 500


class ChatHistory:
    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.expanduser("~"), ".eva", "chat-history.jsonl")
        self._lock = threading.Lock()
        self._items = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            return [r for r in rows if isinstance(r, dict) and r.get("text")][-MAX_KEEP:]
        except (OSError, ValueError):
            return []

    def add(self, role: str, text: str, when=None):
        text = str(text or "").strip()
        if not text:
            return None
        item = {"role": "me" if role == "me" else "eva", "text": text[:20000], "ts": when or time.time()}
        with self._lock:
            self._items.append(item)
            trim = len(self._items) > MAX_KEEP + 50
            if trim:
                self._items = self._items[-MAX_KEEP:]
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                if trim:
                    with open(self.path, "w", encoding="utf-8") as f:
                        f.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in self._items)
                else:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return item

    def put_card(self, card: dict):
        """A finished task card (core/task_card.py), kept in the conversation where it happened;
        the same card again (a replay kept later, an undo) replaces it in place."""
        if not isinstance(card, dict) or not card.get("id"):
            return None
        item = {"role": "card", "text": str(card.get("task") or "task")[:300], "card": card,
                "ts": card.get("started") or time.time()}
        with self._lock:
            for i, old in enumerate(self._items):
                if old.get("role") == "card" and (old.get("card") or {}).get("id") == card["id"]:
                    self._items[i] = item
                    self._rewrite()
                    return item
            self._items.append(item)
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
        return item

    def card(self, card_id):
        with self._lock:
            for it in reversed(self._items):
                if it.get("role") == "card" and (it.get("card") or {}).get("id") == card_id:
                    return dict(it["card"])
        return None

    def _rewrite(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(i, ensure_ascii=False, default=str) + "\n" for i in self._items)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def recent(self, n=60):
        with self._lock:
            return list(self._items[-n:])

    def clear(self):
        with self._lock:
            self._items = []
            try:
                os.remove(self.path)
            except OSError:
                pass


def clean_person_text(text: str) -> str:
    """The person's own words, without what the framework attached (the
    selected-text block, the marked-area note)."""
    t = str(text or "")
    for marker in ("\n\n[The person had this text selected", "\n\n[The person marked the screen area"):
        if marker in t:
            t = t.split(marker)[0] + ("  (with the text you selected)" if "selected" in marker else
                                      "  (about the area you marked)")
    return t.strip()
