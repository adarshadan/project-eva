"""Eva core -- reading the internet without driving a browser (after Agent-Reach).

Three channels, each with an ordered fallback, each refusing private and
internal addresses:
  read_web_page   a page as clean text: Jina Reader (r.jina.ai) first -- it
                  renders JavaScript pages and returns readable Markdown --
                  then a direct fetch with the HTML stripped. Challenge pages
                  ("Just a moment...") are recognised and reported, not
                  returned as content. Privacy: Jina Reader sees the URL; set
                  WEB_READER = "direct" in config.py to keep requests local.
  youtube_transcript   a video's captions as text (youtube-transcript-api).
  read_feed       the newest items of an RSS/Atom feed (feedparser).
Everything returned is third-party text, so it taints the turn like a page.
The browser tools remain for pages that need clicking, a login, or a human.
"""
import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import config
from logger import get_logger

log = get_logger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 24000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
_CHALLENGE = ("just a moment...", "performing security verification", "attention required! | cloudflare",
              "verify you are human", "requiring captcha", "enable javascript and cookies to continue")


def public_url(url: str):
    """(normalised url, None) or (None, reason). Only public http(s) hosts."""
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url if url and "://" not in url else url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None, "only http(s) web addresses can be read"
    if p.username or p.password:
        return None, "addresses with a name or password in them are not read"
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return None, "addresses on this computer or the local network are not read this way"
    try:
        for *_, addr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return None, f"{host} is a private or internal address"
    except Exception as e:
        return None, f"could not find {host}: {e}"
    return url, None


def is_challenge(text: str) -> bool:
    head = (text or "")[:4000].casefold()
    return any(marker in head for marker in _CHALLENGE) and len(text) < 20000


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(raw: str):
    p = _Text()
    try:
        p.feed(raw)
    except Exception:
        pass
    text = html.unescape("".join(p.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return " ".join(p.title.split()), text


def _get(url, headers=None, timeout=30):
    import requests
    with requests.get(url, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout, stream=True,
                      allow_redirects=True) as r:
        r.raise_for_status()
        body = r.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise ValueError("the page is larger than 5 MB")
        final = r.url
        ctype = r.headers.get("content-type", "")
        enc = r.encoding or "utf-8"
    # A redirect may land somewhere private; check where it ended up.
    if final != url:
        ok, why = public_url(final)
        if not ok:
            raise ValueError(f"the page redirected to a disallowed address ({why})")
    return body.decode(enc, errors="replace"), ctype, final


def _trim(text):
    return (text[:MAX_TEXT], len(text) > MAX_TEXT)


def read_web_page(url: str):
    target, why = public_url(url)
    if not target:
        return {"ok": False, "error": why}
    attempts = []
    if getattr(config, "WEB_READER", "jina") == "jina":
        try:
            text, _ctype, _ = _get(f"https://r.jina.ai/{target}", {"Accept": "text/plain", "X-Return-Format": "markdown"})
            if is_challenge(text):
                attempts.append("reader: the site showed a human-verification page")
            elif len(text.strip()) > 200:
                m = re.search(r"^Title:\s*(.+)$", text, re.M)
                body, cut = _trim(text)
                return {"ok": True, "url": target, "title": (m.group(1).strip() if m else ""), "text": body,
                        "via": "reader", **({"truncated": True} if cut else {})}
            else:
                attempts.append("reader: almost no text came back")
        except Exception as e:
            attempts.append(f"reader: {type(e).__name__}: {str(e)[:120]}")
    try:
        raw, ctype, final = _get(target)
        if "html" in ctype or raw.lstrip()[:1] == "<":
            title, text = html_to_text(raw)
        else:
            title, text = "", raw
        if is_challenge(text) or is_challenge(raw[:6000]):
            attempts.append("direct: the site showed a human-verification page")
        elif len(text.strip()) > 80:
            body, cut = _trim(text)
            return {"ok": True, "url": final, "title": title, "text": body, "via": "direct",
                    **({"truncated": True} if cut else {})}
        else:
            attempts.append("direct: the page has almost no text without JavaScript")
    except Exception as e:
        attempts.append(f"direct: {type(e).__name__}: {str(e)[:120]}")
    log.info(f"read_web_page could not read {target}: {attempts}")
    return {"ok": False, "url": target, "error": "could not read the page this way: " + "; ".join(attempts),
            "hint": "open it with browser_open and read it with browser_extract instead"}


# ---- YouTube --------------------------------------------------------------------------
def video_id(url: str):
    u = str(url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", u):
        return u
    p = urlparse(u if "://" in u else "https://" + u)
    host = (p.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = p.path.strip("/").split("/")[0]
    elif "youtube" in host:
        if p.path == "/watch":
            vid = (parse_qs(p.query).get("v") or [""])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", p.path)
            vid = m.group(1) if m else ""
    else:
        vid = ""
    return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or "") else None


def _snippets(fetched):
    out = []
    for s in fetched:
        text = s.get("text") if isinstance(s, dict) else getattr(s, "text", "")
        text = " ".join(str(text or "").replace("\n", " ").split())
        if text and text not in ("[Music]", "[Applause]") and (not out or out[-1] != text):
            out.append(text)
    return out


def youtube_transcript(url: str, language: str = "en"):
    vid = video_id(url)
    if not vid:
        return {"ok": False, "error": "that is not a YouTube video address"}
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return {"ok": False, "error": "youtube-transcript-api is not installed (pip install youtube-transcript-api)"}
    langs = [language, "en", "en-US", "en-GB", "hi"] if language else ["en"]
    try:
        if hasattr(YouTubeTranscriptApi, "fetch") or not hasattr(YouTubeTranscriptApi, "get_transcript"):
            api = YouTubeTranscriptApi()
            try:
                fetched = api.fetch(vid, languages=langs)
            except Exception:
                listing = api.list(vid)
                first = next(iter(listing))
                fetched = (first.translate("en") if getattr(first, "is_translatable", False)
                           and not first.language_code.startswith("en") else first).fetch()
        else:
            fetched = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
    except Exception as e:
        name = type(e).__name__
        why = {"TranscriptsDisabled": "captions are turned off for this video",
               "NoTranscriptFound": "this video has no captions in a usable language",
               "VideoUnavailable": "the video is unavailable",
               "RequestBlocked": "YouTube refused the request from this network",
               "IpBlocked": "YouTube refused the request from this network"}.get(name, f"{name}: {str(e)[:160]}")
        return {"ok": False, "error": f"could not get the transcript: {why}"}
    lines = _snippets(fetched)
    if not lines:
        return {"ok": False, "error": "the transcript was empty"}
    text, cut = _trim(" ".join(lines))
    return {"ok": True, "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "text": text,
            **({"truncated": True} if cut else {})}


# ---- feeds ------------------------------------------------------------------------------
def read_feed(url: str, limit: int = 10):
    target, why = public_url(url)
    if not target:
        return {"ok": False, "error": why}
    try:
        import feedparser
    except ImportError:
        return {"ok": False, "error": "feedparser is not installed (pip install feedparser)"}
    try:
        raw, _ctype, final = _get(target)
    except Exception as e:
        return {"ok": False, "error": f"could not fetch the feed: {type(e).__name__}: {str(e)[:160]}"}
    feed = feedparser.parse(raw)
    if not feed.entries:
        return {"ok": False, "error": "no feed items found at that address (is it an RSS or Atom feed?)"}
    items = []
    for e in feed.entries[:max(1, min(int(limit or 10), 30))]:
        summary = html_to_text(e.get("summary", "") or "")[1]
        items.append({"title": " ".join(str(e.get("title", "")).split()), "link": e.get("link", ""),
                      "published": e.get("published", "") or e.get("updated", ""), "summary": summary[:400]})
    return {"ok": True, "feed": " ".join(str(feed.feed.get("title", "")).split()), "url": final, "items": items}
