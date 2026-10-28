"""Measures the models Eva could use, on YOUR key, so the choice is made on numbers.

    python tools\\model_probe.py                 # the configured models + Nemotron 3.5 Super VL
    python tools\\model_probe.py MODEL [MODEL]   # just these; "provider:model" works too
                                                 # (openrouter:..., ollama:qwen3:8b -- see providers.py)

For each model, 3 rounds of:
  * a short reply                -> latency to the first answer, total time
  * a tool call                  -> did it call the right tool with the right argument?
  * finding a target in a picture (vision models only) -> how far off, in pixels, and in
    which coordinate format it answered (the same test vision.calibrate runs)
with reasoning switched off where the model accepts it, as Eva runs it. Nothing is
changed; the result is printed and written to tools\\model_probe_result.txt.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import config  # noqa: E402

CANDIDATE = "nvidia/nemotron-3.5-super-vl-120b-a12b"
TOOLS = [{"type": "function", "function": {
    "name": "launch_app", "description": "Start an installed program by its plain name.",
    "parameters": {"type": "object", "properties": {"name_or_path": {"type": "string"}},
                   "required": ["name_or_path"]}}}]


def client(model=None):
    import providers
    return providers.client(model or config.MODEL_ID, timeout=60, max_retries=0)


CALL_LIMIT = 60          # seconds per request, however slowly it trickles in
MODEL_LIMIT = 180        # seconds per model: a queued model is reported, not waited on forever


def call(c, model, messages, tools=None, think_off=True):
    import providers
    kw = dict(model=providers.parse(model)[1], messages=messages, max_tokens=1024, temperature=0.2, stream=True)
    if tools:
        kw.update(tools=tools, tool_choice="auto")
    if think_off and providers.is_nim(model):
        kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.monotonic()
    first, text, tool = None, "", {}
    try:
        stream = c.chat.completions.create(**kw)
        for ch in stream:
            if time.monotonic() - t0 > CALL_LIMIT:
                stream.close()
                return {"error": f"no complete answer in {CALL_LIMIT} s", "total": time.monotonic() - t0}
            if not ch.choices:
                continue
            d = ch.choices[0].delta
            if (d.content or d.tool_calls) and first is None:
                first = time.monotonic() - t0
            text += d.content or ""
            for tc in d.tool_calls or []:
                e = tool.setdefault(tc.index, {"name": "", "args": ""})
                if tc.function and tc.function.name:
                    e["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    e["args"] += tc.function.arguments
    except Exception as e:
        if think_off and ("400" in str(e) or "422" in str(e)):
            return call(c, model, messages, tools, think_off=False)
        return {"error": str(e)[:160], "total": time.monotonic() - t0}
    return {"first": first, "total": time.monotonic() - t0, "text": text.strip(), "tools": list(tool.values())}


def picture():
    import vision
    img, target = vision._calibration_picture()
    return vision._encode(img), img.size, target


def probe(model, rounds=3):
    c = client(model)
    out = {"model": model, "reply_s": [], "tool_ok": 0, "vision_px": [], "vision_format": None, "errors": []}
    t0 = time.monotonic()

    def over():
        if time.monotonic() - t0 > MODEL_LIMIT:
            out["errors"].append(f"stopped after {MODEL_LIMIT} s for this model (slow or queued)")
            return True
        return False
    for _ in range(rounds):
        if over():
            break
        r = call(c, model, [{"role": "user", "content": "Reply with one short friendly sentence: hello."}])
        if "error" in r:
            out["errors"].append(r["error"])
            continue
        out["reply_s"].append(round(r["first"] or r["total"], 2))
        r = call(c, model, [{"role": "user", "content": "Please open Notepad."}], TOOLS)
        ok = any(t["name"] == "launch_app" and "notepad" in t["args"].lower() for t in r.get("tools", []))
        out["tool_ok"] += int(ok)
    try:
        import vision
        b64, (w, h), (tx, ty) = picture()
        for _ in range(rounds):
            if out["errors"] and "stopped after" in out["errors"][-1] or over():
                break
            r = call(c, model, [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": vision._locate_prompt("a red circle with the number 7 in it", w, h)}]}])
            if "error" in r:
                out["errors"].append("vision: " + r["error"])
                break
            data = vision.parse_box_reply(r["text"])
            if not isinstance(data, dict) or not data.get("found"):
                out["vision_px"].append(None)
                continue
            best = None
            for conv in vision.CONVENTIONS:
                x1, y1, x2, y2 = vision.to_image(data["x1"], data["y1"], data["x2"], data["y2"], w, h, conv)
                d = (((x1 + x2) / 2 - tx) ** 2 + ((y1 + y2) / 2 - ty) ** 2) ** 0.5
                if best is None or d < best[0]:
                    best = (d, conv)
            out["vision_px"].append(round(best[0]))
            out["vision_format"] = best[1]
    except Exception as e:
        out["errors"].append(f"vision test: {e}"[:160])
    return out


def main():
    models = sys.argv[1:] or list(dict.fromkeys([config.MODEL_ID, config.VISION_MODEL_ID, CANDIDATE]
                                                + list(getattr(config, "VISION_FALLBACK_MODELS", ()))))
    lines = []
    for m in models:
        print(f"Probing {m} ...", flush=True)
        r = probe(m)
        reply = (f"{sum(r['reply_s']) / len(r['reply_s']):.1f} s" if r["reply_s"] else "no answer")
        vis = [p for p in r["vision_px"] if p is not None]
        vline = (f"off by {sorted(vis)[len(vis) // 2]} px ({r['vision_format']})" if vis else
                 ("not a vision model / no answer" if not r["vision_px"] else "could not find the target"))
        line = (f"{m}\n  first answer: {reply}   tool call right: {r['tool_ok']}/3   vision: {vline}"
                + (f"\n  errors: {'; '.join(r['errors'][:3])}" if r["errors"] else ""))
        print(line, flush=True)
        lines.append(line)
    with open(os.path.join(HERE, "model_probe_result.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n" + "\n".join(lines) + "\n")
    print("\nSaved to tools\\model_probe_result.txt -- send it over.")


if __name__ == "__main__":
    main()
