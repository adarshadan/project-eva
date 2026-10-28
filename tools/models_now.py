"""Which models does the free provider serve right now, and do they answer with tools?

2026-10-28: every request to nvidia/nemotron-3-super-120b-a12b came back 404 "Function ... Not found for
account" -- the model Eva used was no longer served to this key. This lists what the provider serves and
sends ONE tiny request (with a tool) to each likely candidate, so the replacement is chosen from facts.

    python tools/models_now.py            -> tools/models_now.txt
    python tools/models_now.py z-ai/glm-5.3 openai/gpt-oss-20b     (only these)
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

OUT = os.path.join(HERE, "tools", "models_now.txt")
WANT = ("nemotron", "qwen3", "qwen2.5-vl", "kimi", "deepseek", "gpt-oss", "glm", "minimax", "llama-4",
        "mistral-large", "mistral-medium", "gemma-3", "gemma-4", "llama-3.3")
TOOL = [{"type": "function", "function": {"name": "get_current_time", "description": "Current time.",
                                           "parameters": {"type": "object", "properties": {}}}}]


def main():
    import config
    import providers
    lines = [f"models_now {time.strftime('%Y-%m-%d %H:%M')}  configured: {config.MODEL_ID} | {config.VISION_MODEL_ID}"]
    c = providers.client("nvidia/x", timeout=40, max_retries=0)
    try:
        ids = sorted(m.id for m in c.models.list().data)
    except Exception as e:
        lines.append(f"list failed: {type(e).__name__}: {e}")
        ids = []
    lines.append(f"{len(ids)} models served")
    cand = [i for i in ids if any(w in i.lower() for w in WANT)]
    lines.append("candidates: " + ", ".join(cand))
    chosen = sys.argv[1:] or ([config.MODEL_ID, config.VISION_MODEL_ID] + cand[:30])
    for ref in chosen:
        model = providers.parse(ref)[1]
        t = time.monotonic()
        try:
            r = c.chat.completions.create(model=model, max_tokens=1200, tools=TOOL, tool_choice="auto",
                                          messages=[{"role": "user", "content": "What time is it? Use the tool."}])
            m = r.choices[0].message
            tc = [x.function.name for x in (m.tool_calls or [])]
            lines.append(f"OK    {model:<55} {time.monotonic() - t:5.1f}s  tool_call={tc or 'none'}  "
                         f"text={(m.content or '')[:40]!r}")
        except Exception as e:
            lines.append(f"FAIL  {model:<55} {time.monotonic() - t:5.1f}s  {type(e).__name__}: {str(e)[:120]}")
        time.sleep(2)                                  # well under the per-minute limit
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    lines.append("DONE")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
