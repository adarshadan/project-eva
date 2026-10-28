// Checks the life layer's logic (blink timing, mouth envelope, gain) against a
// stub expression manager. Verifies the maths and bounds, not how it looks.
// Usage: node tools/life_test.js
const fs = require('fs');
const src = fs.readFileSync(__dirname + '/../web/avatar_3d.html', 'utf8');
const a = src.indexOf('function T(key, dflt)');
const b = src.indexOf('}\n', src.indexOf("return (typeof v === 'number'")) + 2;
const c = src.indexOf('window.EVA_LIFE = true;');
const d = src.indexOf('// ---- the channels the controller owns');
const code = src.slice(a, b) + '\n' + src.slice(c, d);

const values = {};
const em = { getExpression: (n) => ['blink','aa','oh','ih'].includes(n) ? {} : null,
             setValue: (n, v) => { values[n] = v; } };
const win = { EVA_TUNE: {} };
const factory = new Function('window', 'vrmInstance', 'console', code + '\nreturn { life, lifeUpdate };');
const { life, lifeUpdate } = factory(win, { expressionManager: em }, console);

let maxBlink = 0, blinks = 0, prev = 0, maxAa = 0;
for (let t = 0; t < 30; t += 1 / 30) {                       // 30 s idle
  lifeUpdate(t, 1 / 30);
  if (values.blink > maxBlink) maxBlink = values.blink;
  if (prev < 0.5 && values.blink >= 0.5) blinks++;
  prev = values.blink;
  if (values.aa !== 0 && values.aa !== undefined) throw new Error('mouth moved while silent');
}
if (maxBlink < 0.95) throw new Error('blink never closed fully: ' + maxBlink);
if (blinks < 4 || blinks > 20) throw new Error('unrealistic blink count in 30s: ' + blinks);

win.setSpeaking(true);
let varied = new Set();
for (let t = 30; t < 40; t += 1 / 30) {
  lifeUpdate(t, 1 / 30);
  for (const k of ['aa', 'oh', 'ih', 'blink']) if (values[k] < 0 || values[k] > 1) throw new Error(k + ' out of range');
  if (values.aa > maxAa) maxAa = values.aa;
  varied.add(Math.round(values.aa * 10));
}
if (maxAa < 0.25 || maxAa > 0.7) throw new Error('mouth opening out of range: ' + maxAa);
if (varied.size < 4) throw new Error('mouth motion is not varied');

win.setSpeaking(false);
for (let t = 40; t < 42; t += 1 / 30) lifeUpdate(t, 1 / 30);
if (values.aa > 0.02) throw new Error('mouth stayed open after speech ended: ' + values.aa);

win.EVA_TUNE.MOUTH_GAIN = 2; win.setSpeaking(true); let big = 0;
for (let t = 42; t < 50; t += 1 / 30) { lifeUpdate(t, 1 / 30); big = Math.max(big, values.aa); }
if (big <= maxAa * 0.9) throw new Error('MOUTH_GAIN has no effect');
console.log('life layer OK: blinks/30s=' + blinks + ' max mouth=' + maxAa.toFixed(2));
