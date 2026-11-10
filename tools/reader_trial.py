"""The fast reader on real pages, with each candidate model: time, size, and the extract itself.

    python tools/reader_trial.py [model ...]      -> tools/reader_trial.txt
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = os.path.join(HERE, "tools", "reader_trial.txt")
PAGES = [("https://en.wikipedia.org/wiki/Cyclone", "How are tropical cyclones named, and who decides the names?"),
         ("https://docs.python.org/3/library/subprocess.html", "How do I run a command with a timeout and capture its output?")]


def main():
    import config
    import providers
    from core import reader, web_reach
    lines = [f"reader_trial {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
        ids = sorted(m.id for m in providers.client("nvidia/x", timeout=30).models.list().data)
        small = [i for i in ids if any(w in i.lower() for w in ("nano", "lightning", "flash", "mini", "small"))]
        lines.append("catalogue (small/fast names): " + ", ".join(small))
    except Exception as e:
        lines.append(f"catalogue: {type(e).__name__}: {e}")
    models = sys.argv[1:] or [config.READER_MODEL]
    for url, ask in PAGES:
        page = web_reach.read_web_page(url)
        key = reader._longest_text(page)
        lines.append(f"\n== {url}: {len(page.get(key) or '')} chars; request: {ask}")
        for m in models:
            config.READER_MODEL = m
            reader._state["resting_until"] = 0
            t = time.monotonic()
            out = reader.digest("read_web_page", dict(page), ask)
            took = time.monotonic() - t
            if out.get("extract"):
                lines.append(f"-- {m}: {took:.1f} s, {len(out[key])} chars\n{out[key][:1500]}")
            else:
                lines.append(f"-- {m}: no extract after {took:.1f} s")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
