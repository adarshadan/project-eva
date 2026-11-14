# Phase 4 -- Listening to music and dancing (implementation plan)

Ref: docs/life/LIFE_DESIGN.md Phase 4. Follows the Phase 1 pattern (background sensor -> GUI-thread QTimer ->
`avatar_ui` bridge -> page entry point).

## 1. music.py (new, numpy only; the audio library is imported lazily)
- Capture: `PyAudioWPatch` (PyAudio fork with WASAPI loopback; module `pyaudiowpatch`, so it does not
  clash with the `PyAudio` the microphone uses). Pinned in requirements.txt, Windows only. The default
  speakers' loopback device is opened in callback mode; callbacks only queue bytes. If the module is
  missing (or not Windows) the feature is off and one warning is logged. No microphone is ever opened.
- Pure analysis (testable with synthetic signals):
  - `OnsetDetector`: Hann frames of 1024 samples, hop = sr/100 (100 Hz envelope), rfft, log-compressed
    spectral flux (onset strength), plus RMS and low (<200 Hz) / high (>4 kHz) band energy.
  - `estimate_tempo(env, fps)`: autocorrelation of the detrended onset envelope (last 8 s), 70-180 BPM,
    harmonic support (lag 2L), a mild prior around 120 BPM against octave errors; returns bpm + confidence
    (normalised autocorrelation peak).
  - `beat_phase(env, fps, bpm)`: comb score over the last 6 s picks the beat offset; `BeatTracker` smooths
    the anchor so phase is continuous.
  - `MusicAnalyzer.feed(mono)` / `.state()` -> `{playing, bpm, phase, energy, low, high, conf}`.
    "Is it music": sustained energy (>= 80 % of the last 4 s above the floor) AND steady beat (confidence
    over a threshold) for ~3 s to switch on; hysteresis and 1.5 s of silence to switch off. Speech,
    notifications and silence never qualify.
- `MusicListener`: background thread; reads blocks, pads silence when the loopback delivers nothing
  (WASAPI loopback is silent while nothing plays), feeds the analyser, keeps `latest()` under a lock.
  Backend injectable (tests use a fake). Audio is analysed in memory only: nothing is written or sent.

## 2. Wiring
- config.py: `EVA_MUSIC = True`, `EVA_MUSIC_HZ = 20`, `EVA_MUSIC_DANCE = True`.
- eva_app/life.py: a QTimer (GUI thread) at EVA_MUSIC_HZ reads `listener.latest()` and calls
  `avatar.set_music(state)`; while not playing only the transition is sent.
- avatar_ui.py: `set_music(state)` -> `setMusic(json)`; `_push_gesture_settings` sets `window.EVA_MUSIC_DANCE`.

## 3. Page (web/avatar_3d.html, one block before `function playClip`, one hook line in `presenceStep`)
- `window.setMusic(m)` validates and stores with receipt time. Data older than 1.5 s means "stopped".
- `danceStep(frame, o, t, dt, now, idleLike, P)`: engage weight eases in/out (~1 s). Dances only if music
  is playing and fresh, `EVA_MUSIC_DANCE !== false`, idleLike, and NOT focus mode (`world.focus`).
- Own beat clock locked to the sent phase (extrapolated from receipt, correction rate capped).
- Phrases of 8 or 16 beats; at each boundary a new combination of arm pattern x hip/step pattern x
  mirror is drawn by energy tier (calm sway for slow songs or low energy, mid, high), never equal to the
  previous one; patterns crossfade over one beat. Intensity follows smoothed loudness.
- Output: deltas on `o` plus `frame.squat` (knee bounce on the beat), `frame.hipShift`, `frame.footTap`;
  every dance channel is slew-limited so nothing can snap. Running fidgets finish, new ones are held off.

## 4. Tests
- run_checks.py: analyser on a 120 BPM click track (bpm +-3, phase error < 60 ms, also 100 and 140 BPM and a
  kick+hat pattern); silence and speech-like irregular noise bursts -> playing False; missing library
  -> feature off, no crash, one log; listener thread with a fake backend reaches playing=True.
- tools/motion_test.js: with music fed in she bounces on the beat (bounded, no snaps), phrases vary and never
  repeat back to back, intensity follows energy, focus mode / no music / stale music -> no dance.
- Run `node tools/motion_test.js` and `python3 run_checks.py`.

## 5. Risks (Windows-only to verify)
Loopback behaviour (silence = no callbacks, device change, exclusive-mode apps), whether the level follows
the system volume, the pinned wheel for the person's Python, CPU of the analysis thread, thresholds on
real songs.
