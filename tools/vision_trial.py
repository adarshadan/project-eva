"""Which free vision model reads a word-game board correctly? Six boards with known answers through each.

2026-10-28: live Wordle failed on SEEING -- Eva's eyes called a board with three grey CRANE rows "6 empty
rows". Boards: three real screenshots from her replays (wordlegame.org) and three rendered in Wordle's style
with coloured rows (tools/vision_boards/). Each model is asked for the board as JSON; scored per board (all
rows and the typed row exactly right) and per tile.

    python tools/vision_trial.py [model ...]     -> tools/vision_trial.txt
    python tools/vision_trial.py --nothink [model ...]   as the operator asks: thinking off, 500 tokens
"""
import base64
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = os.path.join(HERE, "tools", "vision_trial.txt")
REPLAY = os.path.join(os.path.expanduser("~"), ".eva", "replays", "2026-10-28_013057-open-the-free-wordle-game-at-wordlegame")
BOARDS = os.path.join(HERE, "tools", "vision_boards")

TRUTH = {  # image -> (scored rows [(WORD, colours g/y/b)], typed but unscored letters)
    os.path.join(REPLAY, "step10.jpg"): ([], "CRANE"),
    os.path.join(REPLAY, "step20.jpg"): ([("CRANE", "bbbbb")] * 3, ""),
    os.path.join(REPLAY, "step30.jpg"): ([], "QCRAN"),
    os.path.join(BOARDS, "boardD.jpg"): ([("CRANE", "bbybb"), ("SLOTH", "bgbbb")], ""),
    os.path.join(BOARDS, "boardE.jpg"): ([("AUDIO", "ybbbg"), ("PIANO", "bbgbg"), ("MANGO", "bgbgg")], "TAN"),
    os.path.join(BOARDS, "boardF.jpg"): ([("STARE", "bbybg"), ("GLADE", "bbgbg"), ("WHALE", "bbgbg"),
                                          ("FLAME", "bbggg")], ""),
}
PROMPT = ("This screenshot shows a Wordle-style word game. Read the board exactly, top to bottom.\n"
          "Reply with ONLY this JSON: {\"scored\": [[\"WORD\", \"colours\"], ...], \"typed\": \"LETTERS\"}\n"
          "scored = every row whose tiles are COLOURED (submitted): colours is one letter per tile, g = green, "
          "y = yellow, b = grey. typed = letters in the next row that are typed but not coloured yet (\"\" if "
          "none). Empty rows are not listed.")
VISION_HINTS = ("vl", "vision", "omni", "gemma-3", "gemma-4", "llama-4", "multimodal", "pixtral", "kimi-vl",
                "mistral-small", "mistral-medium", "nano-vl", "qwen3.5", "phi-4")


NOTHINK = False


def ask(c, model, path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    extra = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}} if NOTHINK else {}
    r = c.chat.completions.create(model=model, max_tokens=500 if NOTHINK else 3000, temperature=0.1, **extra,
                                  messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": PROMPT}]}])
    text = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None, text


def score(got, truth):
    rows, typed = truth
    try:
        g_rows = [(re.sub(r"[^A-Z]", "", str(w).upper()), re.sub(r"[^gyb]", "", str(c).lower()))
                  for w, c in (got or {}).get("scored", [])]
        g_typed = re.sub(r"[^A-Z]", "", str((got or {}).get("typed", "")).upper())
    except Exception:
        return False, 0, 0
    total = sum(10 for _ in rows) + len(typed)
    right = 0
    for i, (w, col) in enumerate(rows):
        if i < len(g_rows):
            gw, gc = g_rows[i]
            right += sum(1 for a, b in zip(w, gw) if a == b) + sum(1 for a, b in zip(col, gc) if a == b)
    right += sum(1 for a, b in zip(typed, g_typed) if a == b)
    exact = g_rows == list(rows) and g_typed == typed
    if len(g_rows) > len(rows):                       # invented rows cost as much as missed ones
        right = max(0, right - 10 * (len(g_rows) - len(rows)))
    return exact, right, max(total, 1)


def main(argv):
    global NOTHINK, OUT
    if argv and argv[0] == "--nothink":
        NOTHINK, argv = True, argv[1:]
        OUT = OUT.replace(".txt", "_nothink.txt")
    import config
    import providers
    c = providers.client("nvidia/x", timeout=90, max_retries=0)
    if argv:
        models = argv
    else:
        ids = sorted(m.id for m in c.models.list().data)
        models = [config.VISION_MODEL_ID.split(":")[-1]] + [i for i in ids if any(h in i.lower() for h in VISION_HINTS)
                                                             and i != config.VISION_MODEL_ID]
    lines = [f"vision_trial {time.strftime('%Y-%m-%d %H:%M')}  {len(TRUTH)} boards, models: {', '.join(models)}"]
    summary = []
    for model in models:
        exact_n, right_n, total_n, secs, errors = 0, 0, 0, 0.0, 0
        lines.append(f"\n{model}")
        for path, truth in TRUTH.items():
            t = time.monotonic()
            try:
                got, raw = ask(c, model, path)
                ok, right, total = score(got, truth)
                lines.append(f"  {os.path.basename(path):<11} {'EXACT' if ok else 'wrong'} {right}/{total}  "
                             f"{time.monotonic() - t:5.1f}s  {json.dumps(got)[:170]}")
            except Exception as e:
                ok, right, total = False, 0, sum(10 for _ in truth[0]) + len(truth[1]) or 1
                errors += 1
                lines.append(f"  {os.path.basename(path):<11} ERROR {type(e).__name__}: {str(e)[:110]}")
            exact_n += ok
            right_n += right
            total_n += total
            secs += time.monotonic() - t
            with open(OUT, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            time.sleep(2.0)
        summary.append(f"  {model:<50} exact {exact_n}/{len(TRUTH)}  tiles {100 * right_n // max(1, total_n)}%  "
                       f"{secs / len(TRUTH):5.1f}s per board  errors {errors}")
        if errors == len(TRUTH):
            lines.append("  (not served / no vision)")
    lines.append("\nSUMMARY")
    lines += summary
    lines.append("DONE")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
