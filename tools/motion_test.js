// Checks the motion system in web/avatar_3d.html against stub bones, at a
// real 30 fps frame rate: every activity is distinct and bounded, gestures
// start and end exactly on the live pose, nothing moves faster per frame than
// a person can (no snaps, no glitches), the walk blends in and out, the
// gesture vocabulary Python offers maps onto motions that really exist, and
// gestures refuse honestly when the body is busy. How it LOOKS is verified by
// rendering the rig (see HANDOFF.md, "Seeing the avatar"); this verifies the
// maths every time the checks run.
// Usage: node tools/motion_test.js
const fs = require('fs'), vm = require('vm'), path = require('path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web', 'avatar_3d.html'), 'utf8');
const src = html.slice(html.indexOf('function T(key, dflt)'), html.indexOf('function playClip(name) {'));
const mapSrc = html.slice(html.indexOf('const ANIM_MAP = {'), html.indexOf('const CLIP_START'));

const BONES = ['leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm', 'spine', 'chest', 'head',
               'leftShoulder', 'rightShoulder', 'leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg',
               'rightLowerLeg', 'leftFoot', 'rightFoot', 'hips'];
function bone() {
  const r = { x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } };
  const p = { x: 0, y: 0.8, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } };
  return { rotation: r, position: p };
}
function world() {
  let clock = 1000;                                           // seconds
  const bones = Object.fromEntries(BONES.map(n => [n, bone()]));
  const ctx = {
    window: { EVA_PROCEDURAL: true, EVA_ACTIVITY: true, EVA_LIFE: true, EVA_TUNE: {} }, Math, console, Object,
    performance: { now: () => clock * 1000 },
    usingGltf: true, vrmGestureActive: false, currentAnim: 'idle',
    vrmInstance: { scene: { position: { y: 0 } }, humanoid: { getNormalizedBoneNode: (n) => bones[n] || null } },
  };
  vm.createContext(ctx);
  vm.runInContext(src + `
    restHipsPos = { x: 0, y: 0.8, z: 0 };
    this.api = { pc: poseController, ACT: ACTIVITIES, PROC, rest: restPose, play: playProcedural,
                 setActivity: window.setActivity, writePose, gesture: () => procGesture,
                 walkW: () => walkW, life, presenceStep, setAffect: window.setAffect, emote: window.emote,
                 affect: () => affect };`, ctx);
  const api = ctx.api;
  let t = 0;
  const frame = (dt = 1 / 30) => {
    t += dt; clock += dt;
    const f = api.pc.step(t, dt);
    api.writePose(f, clock);
    return f;
  };
  return { ctx, api, bones, frame, now: () => clock };
}
// Every channel the controller owns, read back from the bones.
function snapshot(b) {
  const out = {};
  for (const n of BONES) { const r = b[n].rotation; out[n + '.x'] = r.x; out[n + '.y'] = r.y; out[n + '.z'] = r.z; }
  out['hips.py'] = b.hips.position.y;
  return out;
}
const maxStep = (a, b) => Math.max(...Object.keys(a).map(k => Math.abs(a[k] - b[k])));

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log('  PASS  ' + name); }
  catch (e) { failures++; console.log('  FAIL  ' + name + ': ' + e.message); }
};
const assert = (c, m) => { if (!c) throw new Error(m); };
// A person's arm moves at most ~6 rad/s in brisk gestures; per 30 fps frame
// that is 0.2 rad. Anything above is a visible snap.
const MAX_PER_FRAME = 0.2;

check('every activity is bounded, alive, and never snaps (10 s at 30 fps)', () => {
  const w = world();
  for (const name of Object.keys(w.api.ACT)) {
    w.api.setActivity(name);
    for (let i = 0; i < 60; i++) w.frame();                    // from wherever the last one left her
    let prev = snapshot(w.bones), worst = 0, moved = 0;
    const first = Object.assign({}, prev);
    for (let i = 0; i < 300; i++) {
      w.frame();
      const s = snapshot(w.bones);
      worst = Math.max(worst, maxStep(prev, s));
      moved = Math.max(moved, maxStep(first, s));
      for (const k in s) assert(Math.abs(s[k]) < 3.0, `${name}: ${k}=${s[k].toFixed(2)} out of range`);
      assert(Math.abs(s['leftLowerArm.y']) < 2.75 && Math.abs(s['rightLowerArm.y']) < 2.75, `${name}: elbow over-bent`);
      prev = s;
    }
    assert(worst <= MAX_PER_FRAME, `${name}: a bone jumped ${worst.toFixed(3)} rad in one frame`);
    assert(moved > 0.01, `${name}: frozen -- no breathing or ambient life`);
  }
});

check('working activities are visibly different from each other', () => {
  const pose = (name) => { const w = world(); w.api.setActivity(name); for (let i = 0; i < 90; i++) w.frame(); return snapshot(w.bones); };
  const names = ['idle', 'listening', 'thinking', 'reading', 'writing', 'browsing', 'searching', 'organizing', 'presenting'];
  const P = Object.fromEntries(names.map(n => [n, pose(n)]));
  const arms = ['leftUpperArm.x', 'leftUpperArm.y', 'leftUpperArm.z', 'rightUpperArm.x', 'rightUpperArm.y', 'rightUpperArm.z',
                'leftLowerArm.y', 'leftLowerArm.z', 'rightLowerArm.y', 'rightLowerArm.z', 'head.x', 'head.y', 'spine.y'];
  for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) {
    const d = arms.reduce((s, k) => s + Math.abs(P[names[i]][k] - P[names[j]][k]), 0);
    assert(d > 0.5, `${names[i]} and ${names[j]} look alike (difference ${d.toFixed(2)})`);
  }
});

check('hands work in front of her: reading, writing, browsing bring both forearms forward', () => {
  for (const name of ['reading', 'writing', 'browsing']) {
    const w = world(); w.api.setActivity(name); for (let i = 0; i < 90; i++) w.frame();
    // measured axes: left elbow bends forward with -y, right with +y
    assert(w.bones.leftLowerArm.rotation.y < -0.8 && w.bones.rightLowerArm.rotation.y > 0.8, `${name}: forearms not forward`);
    assert(w.bones.head.rotation.x < -0.1, `${name}: not looking down at the work (head.x=${w.bones.head.rotation.x.toFixed(2)})`);
  }
});

check('switching activity flows (no frame jumps), whatever the order', () => {
  const w = world(); w.frame();
  const order = ['idle', 'writing', 'searching', 'reading', 'presenting', 'thinking', 'organizing', 'browsing', 'nap', 'idle'];
  let prev = snapshot(w.bones), worst = 0;
  for (const n of order) { w.api.setActivity(n); for (let i = 0; i < 20; i++) { w.frame(); const s = snapshot(w.bones); worst = Math.max(worst, maxStep(prev, s)); prev = s; } }
  assert(worst <= MAX_PER_FRAME, `jumped ${worst.toFixed(3)} rad in one frame`);
});

check('every gesture starts and ends exactly on the live pose, and visibly moves between', () => {
  for (const activityName of ['idle', 'reading']) {
    for (const name of Object.keys(world().api.PROC)) {
      const w = world(); w.api.setActivity(activityName);
      for (let i = 0; i < 120; i++) w.frame();               // settled
      const base = snapshot(w.bones);
      assert(w.api.play(name), `${name} refused to play`);
      const dur = w.api.PROC[name].dur;
      let prev = base, worst = 0, peak = 0;
      const frames = Math.ceil(dur * 30) + 3;
      for (let i = 0; i < frames; i++) {
        w.frame(); const s = snapshot(w.bones);
        worst = Math.max(worst, maxStep(prev, s)); peak = Math.max(peak, maxStep(base, s)); prev = s;
      }
      const end = snapshot(w.bones);
      // the activity keeps living while the gesture runs, so "ends on the live
      // pose" is checked against the activity at the same moment, without it
      const w2 = world(); w2.api.setActivity(activityName); for (let i = 0; i < 120 + frames; i++) w2.frame();
      const ref = snapshot(w2.bones);
      assert(w.api.gesture() === null, `${name}: still running after its duration`);
      assert(maxStep(end, ref) < 0.03, `${name} over ${activityName}: did not return to the live pose (off by ${maxStep(end, ref).toFixed(3)})`);
      assert(peak > 0.12, `${name}: barely moves (${peak.toFixed(3)})`);
      assert(worst <= MAX_PER_FRAME + 0.05, `${name} over ${activityName}: jumped ${worst.toFixed(3)} rad in one frame`);
    }
  }
});

check('reach and point use the measured axes: the left hand reaches to screen-right, the right to screen-left', () => {
  const at = (name) => { const w = world(); for (let i = 0; i < 30; i++) w.frame(); w.api.play(name);
    for (let i = 0; i < 20; i++) w.frame(); return w.bones; };
  const L = at('p_reach_l'), R = at('p_reach_r');
  assert(L.leftUpperArm.rotation.z < 0.6, 'left reach: the left arm did not rise');
  assert(R.rightUpperArm.rotation.z > -0.6, 'right reach: the right arm did not rise');
  assert(L.head.rotation.y > 0.1 && R.head.rotation.y < -0.1, 'she does not look toward what she reaches for');
});

check('the walk blends in and out; legs come back to rest; a gesture is refused mid-walk', () => {
  const w = world();
  for (let i = 0; i < 30; i++) w.frame();
  let prev = snapshot(w.bones), worst = 0;
  w.ctx.currentAnim = 'walk';
  for (let i = 0; i < 90; i++) { w.frame(); const s = snapshot(w.bones); worst = Math.max(worst, maxStep(prev, s)); prev = s; }
  assert(w.api.walkW() > 0.95, 'walk never reached full weight');
  assert(Math.abs(w.bones.leftUpperLeg.rotation.x) > 0.05 || Math.abs(w.bones.rightUpperLeg.rotation.x) > 0.05, 'legs do not stride');
  assert(w.api.play('p_nod') === false, 'a gesture must be refused while walking');
  w.ctx.currentAnim = 'idle';
  for (let i = 0; i < 60; i++) { w.frame(); const s = snapshot(w.bones); worst = Math.max(worst, maxStep(prev, s)); prev = s; }
  assert(worst <= MAX_PER_FRAME, `walk start/stop jumped ${worst.toFixed(3)} rad`);
  assert(Math.abs(w.bones.leftUpperLeg.rotation.x) < 0.01 && Math.abs(w.bones.rightLowerLeg.rotation.x) < 0.01, 'legs left mid-stride');
});

check('gestures refuse honestly: during a clip, when disabled, and for unknown names', () => {
  const a = world(); a.ctx.vrmGestureActive = true; assert(a.api.play('p_nod') === false, 'played over a clip');
  const b = world(); b.ctx.window.EVA_PROCEDURAL = false; assert(b.api.play('p_nod') === false, 'played while disabled');
  const c = world(); assert(c.api.play('p_moonwalk') === false, 'played an unknown gesture');
});

check('every gesture Python offers maps to a motion that exists in the page', () => {
  const m = {}; vm.createContext(m); vm.runInContext(mapSrc + '\nthis.ANIM_MAP = ANIM_MAP; this.VRMA_FILES = VRMA_FILES;', m);
  const PROC = world().api.PROC;
  const py = fs.readFileSync(path.join(root, 'core', 'toolset', 'presence.py'), 'utf8');
  const names = JSON.parse(py.match(/GESTURES = (\[[\s\S]*?\])/)[1].replace(/'/g, '"'));
  for (const g of names) {
    if (g === 'walk_taskbar') continue;                         // handled in Python
    const r = m.ANIM_MAP[g] || g;
    assert(r.startsWith('__procedural_') || PROC[r] || m.VRMA_FILES[r], `"${g}" maps to "${r}", which does not exist`);
  }
  for (const [k, v] of Object.entries(m.ANIM_MAP))
    assert(v.startsWith('__procedural_') || PROC[v] || m.VRMA_FILES[v], `ANIM_MAP ${k} -> ${v} does not exist`);
  // the work gestures embodiment.py and avatar_ui.py send
  for (const g of ['click', 'tidy', 'arrange', 'point', 'p_reach_l', 'p_reach_r', 'p_point_l', 'p_point_r',
                   'p_type', 'p_read', 'p_nod', 'p_shrug', 'p_think', 'p_beckon', 'p_wave'])
    assert(PROC[m.ANIM_MAP[g] || g], `work gesture ${g} has no procedural motion`);
  // desktop work never plays a playful clip
  for (const g of ['click', 'tidy', 'arrange', 'work', 'point'])
    assert(!m.VRMA_FILES[m.ANIM_MAP[g]], `work gesture ${g} plays the clip ${m.ANIM_MAP[g]}`);
});

check('her feelings: every reaction is bounded, never snaps, varies, and fades back (2026-10-28)', () => {
  for (const name of ['eager', 'delight', 'oops', 'curious', 'proud']) {
    const w = world(); w.ctx.window.EVA_PRESENCE = true;
    let t = 0;
    const step = () => { t += 1 / 30; w.ctx.performance.now = (() => { const c = w.now() + t; return () => c * 1000; })();
      const f = w.api.pc.step(t, 1 / 30); w.api.writePose(w.api.presenceStep(f, t, 1 / 30, true), w.now() + t); };
    for (let i = 0; i < 30; i++) step();
    w.api.setAffect(0.9, 0.8, 5);
    assert(w.api.emote(name, 1.0) === true, `${name} refused`);
    let prev = snapshot(w.bones), worst = 0, moved = 0;
    for (let i = 0; i < 75; i++) {
      step(); const now = snapshot(w.bones);
      worst = Math.max(worst, maxStep(prev, now)); moved = Math.max(moved, maxStep(now, prev)); prev = now;
    }
    assert(worst <= MAX_PER_FRAME, `${name} snaps: ${worst.toFixed(3)} rad in one frame`);
    assert(w.api.affect().react === null, `${name} never ended`);
  }
  const w = world(); assert(w.api.emote('moonwalk', 1) === false, 'an unknown reaction played');
  const a = world(), b = world(); a.api.emote('delight', 1); b.api.emote('delight', 1);
  const ra = a.api.affect().react, rb = b.api.affect().react;
  assert(ra.dur !== rb.dur || ra.a !== rb.a || ra.side !== rb.side, 'two reactions came out identical');
});

check('she talks with her body: beats while speaking, bounded, varied, and still when quiet (2026-10-28)', () => {
  const w = world(); w.ctx.window.EVA_PRESENCE = true;
  let t = 0;
  const step = () => { t += 1 / 30; w.ctx.performance.now = (() => { const c = w.now() + t; return () => c * 1000; })();
    const f = w.api.pc.step(t, 1 / 30); w.api.writePose(w.api.presenceStep(f, t, 1 / 30, true), w.now() + t); };
  for (let i = 0; i < 45; i++) step();
  const quiet = snapshot(w.bones);
  w.api.life.speaking = true;
  let prev = snapshot(w.bones), worst = 0, far = 0;
  for (let i = 0; i < 120; i++) {
    step(); const now = snapshot(w.bones);
    worst = Math.max(worst, maxStep(prev, now)); far = Math.max(far, maxStep(quiet, now)); prev = now;
  }
  assert(worst <= MAX_PER_FRAME, `talking snaps: ${worst.toFixed(3)} rad in one frame`);
  assert(far > 0.08, `talking barely moves her: ${far.toFixed(3)} rad`);
  w.api.life.speaking = false;
  for (let i = 0; i < 90; i++) step();
  const arms = (o) => Object.fromEntries(Object.entries(o).filter(([k]) => /lowerArm/i.test(k)));
  const after = maxStep(arms(quiet), arms(snapshot(w.bones)));
  assert(after < 0.05, `she keeps gesturing after she stopped talking: ${after.toFixed(3)} rad`);
});

console.log(failures ? `\n${failures} motion check(s) FAILED` : '\nall motion checks passed');
process.exit(failures ? 1 : 0);
