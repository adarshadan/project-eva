# Interaction Latency Baseline: Session 2026-11-15 (11:18 – 11:31)

Measured from `eva.log` for each `Heard:` event to the first subsequent `Voice reply:` or `Tool call:` event.

## Summary Metrics
- **Total Measured Turns**: 14
- **Median Latency**: 4.54 s
- **90th Percentile Latency (p90)**: 11.28 s

## 3 Slowest Turns
1. **20.17 s** — Phrase: `'More fast'`
   - *Next Event*: Tool call `adjust_activity({'faster': 'true', ...})` at 11:29:42.818
   - *Cause*: Delayed by HTTP 429 `RateLimitError` on NVIDIA NIM, triggering a 15-second provider hold while scrolling was active.
2. **11.28 s** — Phrase: `'Good to see you both.'`
   - *Next Event*: Voice reply `'Good to see you too!'` at 11:18:42.861
   - *Cause*: Session initialization, provider key validation warnings, and initial cold model call.
3. **10.16 s** — Phrase: `'You can scroll a little bit faster.'`
   - *Next Event*: Tool call `adjust_activity({'faster': True, ...})` at 11:29:08.217
   - *Cause*: Pre-empted by forced vision verification check (`needs_final_look`) and rate limit burst pacing.

## All Turns Chronological Log

| Heard Timestamp | Next Event Timestamp | Latency (s) | User Phrase (Heard) | First Response Event |
| :--- | :--- | :---: | :--- | :--- |
| 11:18:31.583 | 11:18:42.861 | 11.28 | `'Good to see you both.'` | Voice reply: `'Good to see you too!'` |
| 11:25:26.660 | 11:25:31.078 | 4.42 | `'Eva, can you hear this song?'` | Tool call: `listen_internal_audio({'seconds': 8, ...})` |
| 11:25:53.178 | 11:25:57.095 | 3.92 | `"How is this? You can't hear it clearly."` | Tool call: `listen_internal_audio({'seconds': 15, ...})` |
| 11:26:24.453 | 11:26:33.597 | 9.14 | `'Okay, can you dance to it beat?'` | Tool call: `use_tools({'groups': ['body']})` |
| 11:26:51.496 | 11:26:54.637 | 3.14 | `'But is that it?'` | Tool call: `play_animation({'name': 'twirl', ...})` |
| 11:27:14.949 | 11:27:17.099 | 2.15 | `'No.'` | Tool call: `stop_animation({})` |
| 11:27:29.300 | 11:27:33.963 | 4.66 | `'No.'` | Voice reply: `'Okay, I’ll leave it there.'` |
| 11:28:12.836 | 11:28:18.677 | 5.84 | `'Okay, open Times of India website and slowly...'` | Tool call: `use_tools({'groups': ['web_pages', 'schedule']})` |
| 11:28:58.056 | 11:29:08.217 | 10.16 | `'You can scroll a little bit faster.'` | Tool call: `adjust_activity({'faster': True, ...})` |
| 11:29:09.674 | 11:29:19.760 | 10.09 | `'A bit more faster.'` | Tool call: `adjust_activity({'faster': True, ...})` |
| 11:29:22.644 | 11:29:42.818 | 20.17 | `'More fast'` | Tool call: `adjust_activity({'faster': 'true', ...})` |
| 11:30:55.282 | 11:30:57.879 | 2.60 | `'Even closed this website.'` | Tool call: `stop_activity({})` |
| 11:31:27.158 | 11:31:31.453 | 4.29 | `'You should not close the browser if you did...'` | Voice reply: `"Got it — I won't close the browser."` |
| 11:31:40.501 | 11:31:43.163 | 2.66 | `"Well then i'm safe at least."` | Voice reply: `"Glad we're good."` |
