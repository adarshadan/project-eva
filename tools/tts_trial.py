"""Which voice speaks sooner and sounds better: the current online voice (Microsoft Edge) or NVIDIA's free
Magpie (same key as the model, Riva gRPC)?

Eva speaks a reply sentence by sentence and synthesises the next one while the current one plays
(core/speaker.py), so what the person waits for is the FIRST sentence's synthesis. Each engine says the same
lines; seconds are recorded and the audio is kept in tools/tts_samples/ to listen to.

    python tools/tts_trial.py            -> tools/tts_trial.txt, tools/tts_samples/*.wav|*.mp3
"""
import os
import shutil
import sys
import time
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = os.path.join(HERE, "tools", "tts_trial.txt")
SAMPLES = os.path.join(HERE, "tools", "tts_samples")
MAGPIE_FID = "877104f7-e885-42b9-8de8-f6e4c6303969"
LINES = ["Sure.",
         "I've opened Notepad for you.",
         "Wordle won in three guesses, and the word was grist.",
         "I found five files you changed this week in Documents; the largest is the budget spreadsheet, at "
         "two megabytes, and the newest is your meeting notes from this morning."]


def key():
    try:
        import secret_store
        k = secret_store.get("NVIDIA_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    import config
    return config.NVIDIA_API_KEY


def magpie_service():
    import riva.client
    auth = riva.client.Auth(uri="grpc.nvcf.nvidia.com:443", use_ssl=True,
                            metadata_args=[["function-id", MAGPIE_FID], ["authorization", f"Bearer {key()}"]])
    return riva.client, auth, riva.client.SpeechSynthesisService(auth)


def magpie_voices(rc, auth, svc):
    try:
        from riva.client.proto import riva_tts_pb2
        cfg = svc.stub.GetRivaSynthesisConfig(riva_tts_pb2.RivaSynthesisConfigRequest(),
                                              metadata=auth.get_auth_metadata())
        names = []
        for mc in cfg.model_config:
            v = mc.parameters.get("voice_name", "")
            sub = mc.parameters.get("subvoices", "")
            lang = mc.parameters.get("language_code", "")
            if sub:
                names += [f"{v}.{s.split(':')[0]}" for s in sub.split(",")]
            elif v:
                names.append(v)
            if lang:
                names.append(f"({lang})")
        return names
    except Exception as e:
        return [f"could not list voices: {type(e).__name__}: {str(e)[:120]}"]


def magpie(svc, rc, text, voice, path, stream=False):
    t = time.monotonic()
    first = None
    if stream:
        chunks = []
        for r in svc.synthesize_online(text, voice, "en-US", sample_rate_hz=44100,
                                       encoding=rc.AudioEncoding.LINEAR_PCM):
            if first is None:
                first = time.monotonic() - t
            chunks.append(r.audio)
        audio = b"".join(chunks)
    else:
        audio = svc.synthesize(text, voice, "en-US", sample_rate_hz=44100,
                               encoding=rc.AudioEncoding.LINEAR_PCM).audio
        first = time.monotonic() - t
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(audio)
    return first, time.monotonic() - t, len(audio) / 2 / 44100


def edge(text, path):
    import voice
    t = time.monotonic()
    tmp = voice._online_attempt(text)
    took = time.monotonic() - t
    shutil.move(tmp, path)
    return took, took, None


def main():
    os.makedirs(SAMPLES, exist_ok=True)
    lines = [f"tts_trial {time.strftime('%Y-%m-%d %H:%M')}"]
    rc, auth, svc = magpie_service()
    voices = magpie_voices(rc, auth, svc)
    lines.append("Magpie voices: " + ", ".join(voices[:60]))
    en = [v for v in voices if "EN-US" in v.upper() and "." in v]
    picks = [v for v in en if v.split(".")[-1].lower() in ("aria", "sofia", "mia", "louise", "jason", "leo")][:3] \
        or (en[:2] or ["Magpie-Multilingual.EN-US.Aria"])
    totals = {}
    for i, text in enumerate(LINES):
        lines.append(f"\n[{len(text)} chars] {text}")
        runs = [("edge (current)", lambda p: edge(text, p), "mp3")]
        for v in picks:
            runs.append((f"magpie {v.split('.')[-1]}", lambda p, v=v: magpie(svc, rc, text, v, p), "wav"))
        runs.append((f"magpie {picks[0].split('.')[-1]} streaming",
                     lambda p: magpie(svc, rc, text, picks[0], p, stream=True), "wav"))
        for name, fn, ext in runs:
            path = os.path.join(SAMPLES, f"{i + 1}-{name.split()[0]}-{name.split()[1] if ' ' in name else ''}"
                                         f"{'-stream' if 'streaming' in name else ''}.{ext}")
            try:
                first, total, secs = fn(path)
                lines.append(f"  {name:<28} first audio {first:5.2f}s  total {total:5.2f}s"
                             + (f"  ({secs:.1f}s of speech)" if secs else ""))
                t = totals.setdefault(name, [0.0, 0])
                t[0] += first
                t[1] += 1
            except Exception as e:
                lines.append(f"  {name:<28} ERROR {type(e).__name__}: {str(e)[:150]}")
            time.sleep(1.0)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    lines.append("\nmean seconds to first audio:")
    for name, (s, n) in totals.items():
        lines.append(f"  {name:<28} {s / n:5.2f}s over {n}")
    lines.append(f"samples: {SAMPLES}")
    lines.append("DONE")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
