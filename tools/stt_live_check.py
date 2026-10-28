"""The live hearing path, end to end, on kept phrases: WAV -> speech_recognition audio -> voice.recognize
(STT_BACKEND "nvidia") -> text. Proves the raw-PCM call Eva makes while listening, not only the file call
tools/asr_trial.py makes.    python tools/stt_live_check.py [model]
"""
import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)


def main(argv):
    import config
    config.STT_BACKEND = "nvidia"
    config.STT_NVIDIA_MODEL = argv[0] if argv else "canary-1b"
    import speech_recognition as sr
    import voice
    r = sr.Recognizer()
    wavs = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".eva", "hearing", "*.wav")))[-6:]
    print(f"model {config.STT_NVIDIA_MODEL}, {len(wavs)} phrases")
    for w in wavs:
        with sr.AudioFile(w) as src:
            audio = r.record(src)
        t = time.monotonic()
        try:
            text = voice.recognize(r, audio)
        except sr.UnknownValueError:
            text = "(nothing)"
        print(f"  {os.path.basename(w)}  {time.monotonic() - t:.2f}s  {text}")
    import stt_nvidia
    print("resting (a failure fell back to Google)" if stt_nvidia._state["resting_until"] else "no fallback used")


if __name__ == "__main__":
    main(sys.argv[1:])
