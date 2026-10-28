"""What does the microphone actually give? Device, rate, and loudness every half second for 8 seconds.

2026-10-28 "it says Listening but actually it is not": the capture had started, yet no phrase was ever
captured. This tells silence-from-the-device (0 or near 0: muted, wrong device, privacy block) apart from a
normal room (tens) and speech (hundreds+) -- compare with Eva's gate in eva.log ("gate 94").
    python tools/mic_probe.py      -> tools/mic_probe.txt
"""
import audioop
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "tools", "mic_probe.txt")


def main():
    import pyaudio
    pa = pyaudio.PyAudio()
    lines = [f"mic_probe {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
        d = pa.get_default_input_device_info()
        lines.append(f"default input: #{d['index']} {d['name']} ({int(d['defaultSampleRate'])} Hz, "
                     f"{d['maxInputChannels']} ch)")
    except Exception as e:
        lines.append(f"no default input device: {e}")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0 and info.get("hostApi") == 0:
            lines.append(f"  input #{i}: {info['name']}")
    rate = 44100
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True, frames_per_buffer=1024)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8:
        frames = [stream.read(1024, exception_on_overflow=False) for _ in range(int(rate * 0.5 / 1024))]
        buf = b"".join(frames)
        lines.append(f"  {time.monotonic() - t0:4.1f}s  rms {audioop.rms(buf, 2):6d}  peak {audioop.max(buf, 2):6d}")
    stream.close()
    pa.terminate()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
