"""Eva -- is that a video?

Every native crash on 2026-10-11 (13:13 closing a full-screen video, 15:34
clicking a YouTube thumbnail that started playing, 17:10 clicking a
Picture-in-Picture video) happened as she moved onto a PLAYING video, with the
GUI thread frozen for 3 s first. Whatever the driver-level cause, the trigger
is shared, so she now keeps her body off video windows: she points at them
from where she is, and does not fight a video or Picture-in-Picture window for
the top of the z-order. Qt-free; the Windows lookups fail soft.
"""
import re
import sys

VIDEO_TITLE = re.compile(r"(- YouTube( - |$)|Picture[- ]in[- ]Picture|Netflix|Prime Video|Disney\+|Hotstar|JioCinema|"
                         r"Twitch|Vimeo|VLC media player|Movies & TV|Media Player|\.(mp4|mkv|avi|mov|webm)\b)", re.I)


def is_video_title(title) -> bool:
    return bool(VIDEO_TITLE.search(title or ""))


def window_title(hwnd) -> str:
    if sys.platform != "win32" or not hwnd:
        return ""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buf, 512)
        return buf.value
    except Exception:
        return ""


def window_at(x, y):
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.WindowFromPoint.restype = ctypes.c_void_p
        u.GetAncestor.restype = ctypes.c_void_p
        h = u.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return u.GetAncestor(ctypes.c_void_p(h), 2) if h else None      # GA_ROOT
    except Exception:
        return None


def video_at(x, y) -> bool:
    return is_video_title(window_title(window_at(x, y)))
