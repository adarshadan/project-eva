"""Both input devices at once for 10 s: which one hears the person? -> tools/mic_compare.txt"""
import audioop, os, sys, threading, time
import pyaudio
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mic_compare.txt")
pa = pyaudio.PyAudio()
devs = [i for i in range(pa.get_device_count())
        if pa.get_device_info_by_index(i).get("maxInputChannels", 0) > 0 and pa.get_device_info_by_index(i).get("hostApi") == 0 and i != 0]
res = {}
def rec(i):
    info = pa.get_device_info_by_index(i)
    rate = int(info["defaultSampleRate"])
    try:
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True, input_device_index=i, frames_per_buffer=1024)
    except Exception as e:
        res[i] = (info["name"], f"could not open: {e}"); return
    levels = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10:
        buf = b"".join(s.read(1024, exception_on_overflow=False) for _ in range(int(rate * 0.5 / 1024)))
        levels.append(audioop.rms(buf, 2))
    s.close()
    res[i] = (info["name"], levels)
ts = [threading.Thread(target=rec, args=(i,)) for i in devs]
[t.start() for t in ts]; [t.join() for t in ts]
with open(OUT, "w", encoding="utf-8") as f:
    f.write(time.strftime("%H:%M:%S") + "\n")
    for i, (name, lv) in res.items():
        f.write(f"#{i} {name}: {lv}\n")
pa.terminate()
