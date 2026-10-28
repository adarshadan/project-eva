"""Which free recogniser hears the person best? Every kept phrase (~/.eva/hearing) through each one.

2026-10-28: the kept phrases showed the start of the voice is captured (13 of 14 with a full run-up), yet
words were misheard by the free Google recogniser ("Centre button" for "enter button", "you cannot become
that sell"). NVIDIA's free catalogue serves speech recognisers with the same key as the model, over Riva
gRPC (grpc.nvcf.nvidia.com, function-id per model -- build.nvidia.com/<model>/api):

    whisper-large-v3          b702f636-f60c-4a3d-a6f4-f3568c13bd7d   (language "en")
    parakeet-tdt-0.6b-v2      d3fe9151-442b-4204-a70d-5fcc597fd610   (en-US)
    canary-1b                 b0e8b4a5-217c-40b7-9b96-17d84e666317   (en-US)

    python tools/asr_trial.py          -> tools/asr_trial.txt  (needs: pip install nvidia-riva-client)
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = os.path.join(HERE, "tools", "asr_trial.txt")
HEARING = os.path.join(os.path.expanduser("~"), ".eva", "hearing")

NVIDIA = {"whisper-large-v3": ("b702f636-f60c-4a3d-a6f4-f3568c13bd7d", "en"),
          "parakeet-tdt-0.6b-v2": ("d3fe9151-442b-4204-a70d-5fcc597fd610", "en-US"),
          "canary-1b": ("b0e8b4a5-217c-40b7-9b96-17d84e666317", "en-US")}


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


def google(path):
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(path) as src:
        audio = r.record(src)
    try:
        return r.recognize_google(audio)
    except sr.UnknownValueError:
        return ""


def nvidia(path, fid, lang, api_key):
    import riva.client
    auth = riva.client.Auth(uri="grpc.nvcf.nvidia.com:443", use_ssl=True,
                            metadata_args=[["function-id", fid], ["authorization", f"Bearer {api_key}"]])
    asr = riva.client.ASRService(auth)
    cfg = riva.client.RecognitionConfig(language_code=lang, max_alternatives=1,
                                        enable_automatic_punctuation=True, audio_channel_count=1)
    riva.client.add_audio_file_specs_to_config(cfg, path)
    with open(path, "rb") as f:
        data = f.read()
    resp = asr.offline_recognize(data, cfg)
    return " ".join(r.alternatives[0].transcript.strip() for r in resp.results if r.alternatives).strip()


def main():
    api_key = key()
    heard = {}
    try:
        with open(os.path.join(HEARING, "heard.jsonl"), encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                heard[d["at"]] = d.get("text", "")
    except OSError:
        pass
    wavs = sorted(glob.glob(os.path.join(HEARING, "*.wav")))
    lines = [f"asr_trial {time.strftime('%Y-%m-%d %H:%M')}  {len(wavs)} phrases"]
    totals = {}
    for w in wavs:
        stem = os.path.splitext(os.path.basename(w))[0]
        lines.append(f"\n{stem}  (heard live: {heard.get(stem, '?')!r})")
        engines = [("google", lambda p: google(p))] + [
            (n, lambda p, fid=fid, lang=lang: nvidia(p, fid, lang, api_key)) for n, (fid, lang) in NVIDIA.items()]
        for name, fn in engines:
            t = time.monotonic()
            try:
                text = fn(w)
                ok = True
            except Exception as e:
                text, ok = f"ERROR {type(e).__name__}: {str(e)[:150]}", False
            dt = time.monotonic() - t
            s = totals.setdefault(name, [0, 0.0, 0])
            s[0] += ok
            s[1] += dt
            s[2] += 1
            lines.append(f"  {name:<22} {dt:5.2f}s  {text}")
            time.sleep(1.5)                      # about 30 a minute: under the shared limit
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    lines.append("\nper engine: answered / phrases, mean seconds")
    for name, (ok, secs, n) in totals.items():
        lines.append(f"  {name:<22} {ok}/{n}  {secs / max(1, n):.2f}s")
    lines.append("DONE")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
