"""Read-only: Windows OCR through a new engine per read (winocr) vs the kept engine (screen_text).

    python tools\\ocr_bench.py

Captures the screen once, reads it 3 times each way, prints the times and whether both found the
same words. Writes tools\\ocr_bench.txt.
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import mss  # noqa: E402
from PIL import Image  # noqa: E402
import screen_text as st  # noqa: E402


def main():
    with mss.mss() as m:
        shot = m.grab(m.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    lines = [f"screen {img.size[0]}x{img.size[1]}"]
    import winocr
    t0 = time.perf_counter(); st._win_engine(); lines.append(f"kept engine created in {1000*(time.perf_counter()-t0):.0f} ms")
    old_t, new_t, old_w, new_w = [], [], None, None
    for _ in range(3):
        t0 = time.perf_counter(); res = winocr.recognize_pil_sync(img, "en"); old_t.append(time.perf_counter() - t0)
        old_w = [w["text"] for line in res["lines"] for w in line["words"]]
        t0 = time.perf_counter(); new = st._win_ocr_words(img); new_t.append(time.perf_counter() - t0)
        new_w = [w[0] for w in new]
    lines.append(f"winocr (new engine each time): median {1000*statistics.median(old_t):.0f} ms  {[round(1000*x) for x in old_t]}")
    lines.append(f"kept engine:                   median {1000*statistics.median(new_t):.0f} ms  {[round(1000*x) for x in new_t]}")
    lines.append(f"words: {len(old_w)} vs {len(new_w)}; identical: {old_w == new_w}")
    small = img.crop((0, 0, 400, 120))
    t0 = time.perf_counter(); st._win_ocr_words(small); lines.append(f"a button-sized crop: {1000*(time.perf_counter()-t0):.0f} ms")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(HERE, "ocr_bench.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n" + text + "\n")


if __name__ == "__main__":
    main()
