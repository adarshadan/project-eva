"""Live check of the model chain against the real service (two short requests on your key).

    python tools\\provider_smoke.py

1. A model that does not exist first, the configured MODEL_ID behind it: the chain must skip the
   missing one and answer from the real one.
2. Every model in MODEL_ID + MODEL_FALLBACKS: is it usable (key present) and does it answer?
Writes tools\\provider_smoke_result.txt.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import config  # noqa: E402
import providers  # noqa: E402
from core import llm  # noqa: E402


def main():
    lines = []
    real_id, real_fb = config.MODEL_ID, tuple(getattr(config, "MODEL_FALLBACKS", ()))
    try:
        config.MODEL_ID, config.MODEL_FALLBACKS = "nvidia/this-model-does-not-exist", (real_id,)
        t0 = time.monotonic()
        out = llm.call([{"role": "user", "content": "Reply with the single word: ready"}], "Be brief.", [])
        lines.append(f"fallback past a missing model: answered by {providers.describe(llm.last_model())} "
                     f"in {time.monotonic() - t0:.1f} s -> {out.get('content')!r}")
    except Exception as e:
        lines.append(f"fallback past a missing model: FAILED {type(e).__name__}: {str(e)[:200]}")
    finally:
        config.MODEL_ID, config.MODEL_FALLBACKS = real_id, real_fb
    for ref in [real_id, *real_fb]:
        ok, why = providers.usable(ref)
        if not ok:
            lines.append(f"{ref}: not usable -- {why}")
            continue
        try:
            t0 = time.monotonic()
            r = providers.client(ref, timeout=60, max_retries=0).chat.completions.create(
                model=providers.parse(ref)[1], max_tokens=20,
                messages=[{"role": "user", "content": "Reply with the single word: ready"}])
            lines.append(f"{ref}: answers in {time.monotonic() - t0:.1f} s -> "
                         f"{(r.choices[0].message.content or '').strip()[:40]!r}")
        except Exception as e:
            lines.append(f"{ref}: FAILED {type(e).__name__}: {str(e)[:160]}")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(HERE, "provider_smoke_result.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n" + text + "\n")


if __name__ == "__main__":
    main()
