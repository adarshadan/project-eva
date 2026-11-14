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
// Math with a seeded random(): two worlds built the same way behave the same way (the body's weight shifts and
// noise draw random numbers), which the "compare against a reference world" checks below rely on.
function seededMath(seed) {
  const M = Object.create(Math); let a = seed >>> 0;
  M.random = () => { a = (a + 0x6D2B79F5) >>> 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
                     t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  return M;
}
function world(seed = 12345) {
  let clock = 1000;                                           // seconds
  const bones = Object.fromEntries(BONES.map(n => [n, bone()]));
  const ctx = {
    window: { EVA_PROCEDURAL: true, EVA_ACTIVITY: true, EVA_LIFE: true, EVA_TUNE: {} }, Math: seededMath(seed), console, Object,
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
                 setWorld: window.setWorld, presence, acknowledge: window.acknowledge,
                 affect: () => affect, bl, bnoise, BL_NOISE, stridePlant, faceStep, cloth, clothStep, blFollow: _blFollow,
                 bodyLifeApply, setLocomotion: window.setLocomotion,
                 drives, takeDriveIntents: window.takeDriveIntents,
                 driveResult: window.driveResult, setBodyState: window.setBodyState, BEHAVIOURS };`, ctx);
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
// A believable world sample; each check overrides only what it needs.
function worldPayload(over) {
  return Object.assign({
    t: 1, cursor: [40, 40], speed: 0,
    window: { rect: [100, 100, 900, 700], kind: 'editor', title: 'notes.txt - Notepad' },
    keys: 0, idle: 0, full: false, new: null,
    screen: [1920, 1080], eva: [850, 700, 1000, 1080], focus: false,
  }, over || {});
}
// Advances performance.now like the feeling checks do, then runs presenceStep and writes the pose.
function stepper(w) {
  let t = 0;
  const step = (dt = 1 / 30) => {
    t += dt;
    const now = w.now() + t;
    w.ctx.performance.now = () => now * 1000;
    const f = w.api.pc.step(t, dt);
    w.api.writePose(w.api.presenceStep(f, t, dt, true), now);
    return f;
  };
  return { step, now: () => w.now() + t };
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
  let last;
  for (let i = 0; i < 60; i++) { last = w.frame(); const s = snapshot(w.bones); worst = Math.max(worst, maxStep(prev, s)); prev = s; }
  assert(worst <= MAX_PER_FRAME, `walk start/stop jumped ${worst.toFixed(3)} rad`);
  // Phase 3: a standing weight shift softens the unloaded knee (last.bl); what is left over must be no stride
  const bl3 = last.bl || { thighL: 0, kneeR: 0 };
  assert(Math.abs(w.bones.leftUpperLeg.rotation.x - bl3.thighL) < 0.01 && Math.abs(w.bones.rightLowerLeg.rotation.x - bl3.kneeR) < 0.01, 'legs left mid-stride');
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
  const a = world(1), b = world(2); a.api.emote('delight', 1); b.api.emote('delight', 1);   // different seeds: variety is the point
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

check('a new window is looked at within 300 ms, and the eyes never snap (Phase 1)', () => {
  const w = world();
  const run = stepper(w);
  for (let i = 0; i < 30; i++) run.step();                     // settle
  w.api.setWorld(worldPayload({ new: { rect: [10, 200, 300, 400], at: 1, kind: 'window' } }));
  let prev = snapshot(w.bones), worst = 0, aimedInTime = false;
  for (let i = 0; i < 9; i++) {
    run.step();
    if (i < 2 && w.api.presence.aim && w.api.presence.aim.kind === 'new') aimedInTime = true;
    const s = snapshot(w.bones);
    worst = Math.max(worst, maxStep(prev, s)); prev = s;
  }
  assert(aimedInTime, 'aim.kind was not "new" within two frames');
  const aim = w.api.presence.aim;
  assert(aim && Math.abs(w.api.presence.eyeYaw) > 0.7 * Math.abs(aim.yaw),
    `eyes not on the new window (eyeYaw=${w.api.presence.eyeYaw.toFixed(1)}, aim=${aim && aim.yaw.toFixed(1)})`);
  assert(worst <= MAX_PER_FRAME, `looking at a new window snapped ${worst.toFixed(3)} rad in one frame`);
});

check('she looks at the person when spoken to, and a stale target decays away (Phase 1)', () => {
  const w = world();
  const run = stepper(w);
  w.api.setWorld(worldPayload());
  for (let i = 0; i < 5; i++) run.step();
  w.api.acknowledge();
  for (let i = 0; i < 10; i++) run.step();
  const aim = w.api.presence.aim;
  assert(aim && aim.kind === 'person', `acknowledge did not turn her to the person (${aim && aim.kind})`);
  assert(Math.abs(w.api.presence.eyeYaw) < 6, `eyes not on the person (${w.api.presence.eyeYaw.toFixed(1)})`);
  // a new window is looked at, then nothing new happens for four seconds
  w.api.setWorld(worldPayload({ new: { rect: [10, 200, 300, 400], at: 2, kind: 'window' } }));
  for (let i = 0; i < 120; i++) run.step();
  const stale = w.api.presence.aim;
  assert(stale && stale.salience < 0.15, `stale target still salient (${stale && stale.salience.toFixed(2)})`);
  assert(stale.kind !== 'new', 'she still stares at the window that appeared four seconds ago');
});

check('focus mode keeps her still: at most one glance in ten seconds (Phase 1)', () => {
  const range = (values) => Math.max(...values) - Math.min(...values);
  const w = world();
  const run = stepper(w);
  w.api.setWorld(worldPayload({ focus: true, keys: 300 }));
  const head = [], torso = [], deg = Math.PI / 180;
  for (let i = 0; i < 300; i++) {
    run.step();
    head.push(w.api.presence.headYaw * deg);
    torso.push(w.api.presence.torsoYaw * deg);
  }
  assert(range(head) < 0.05, `head moved ${range(head).toFixed(3)} rad in focus mode`);
  assert(range(torso) < 0.05, `torso moved ${range(torso).toFixed(3)} rad in focus mode`);
  assert(w.api.presence.glanceCount <= 1, `${w.api.presence.glanceCount} glances in ten seconds of focus`);
  // control: the same world without focus moves her head more
  const cw = world();
  const crun = stepper(cw);
  cw.api.setWorld(worldPayload({ new: { rect: [10, 200, 300, 400], at: 1, kind: 'window' } }));
  const control = [];
  for (let i = 0; i < 300; i++) { crun.step(); control.push(cw.api.presence.headYaw * deg); }
  assert(range(control) > range(head) + 0.05, 'the non-focus control did not move more');
  // and a bored, non-focused world still fidgets
  const bw = world();
  const brun = stepper(bw);
  bw.api.setWorld(worldPayload());
  bw.api.presence.boredom = 3;
  bw.api.presence.nextFidget = 0;
  let fidgeted = false;
  for (let i = 0; i < 120; i++) { brun.step(); if (bw.api.presence.fidget) fidgeted = true; }
  assert(fidgeted, 'bored but not focused: she never fidgets');
});

// ---- Phase 3: a body that moves like a person ---------------------------------------------------------
const range3 = (xs) => Math.max(...xs) - Math.min(...xs);
const corr3 = (a, b) => {
  const n = a.length; let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; }
  return sab / Math.sqrt(saa * sbb + 1e-12);
};
// Run `seconds` of the full path (controller + presence + body layer) at `fps`; calls onFrame(snapshot, frame) each frame.
function run3(w, seconds, fps, onFrame) {
  const run = stepper(w), dt = 1 / fps;
  for (let i = 0; i < Math.round(seconds * fps); i++) { const f = run.step(dt); onFrame && onFrame(snapshot(w.bones), f); }
  return run;
}
const CORE = ['spine.x', 'spine.y', 'spine.z', 'chest.x', 'head.x', 'head.y', 'head.z'];

check('Phase 3: idle is never perfectly still but stays bounded, in every 10 s window of a minute', () => {
  const on = world(), off = world(); off.ctx.window.EVA_BODY_LIFE = false;
  const tr = { on: [], off: [] };
  run3(on, 60, 30, (s) => tr.on.push(s));
  run3(off, 60, 30, (s) => tr.off.push(s));
  for (let win = 0; win < 6; win++) {
    const slice = tr.on.slice(win * 300, win * 300 + 300);
    const moved = Math.max(...CORE.map((k) => range3(slice.map((s) => s[k]))));
    assert(moved > 0.012, `window ${win}: she is nearly frozen (largest range ${moved.toFixed(4)} rad)`);
  }
  for (const k of Object.keys(tr.on[0])) {          // bounded: never far outside what the plain body already does
    const lo = Math.min(...tr.off.map((s) => s[k])), hi = Math.max(...tr.off.map((s) => s[k]));
    const slack = k === 'hips.py' ? 0.02 : 0.12;
    for (const s of tr.on) assert(s[k] > lo - slack && s[k] < hi + slack, `${k}=${s[k].toFixed(3)} outside [${lo.toFixed(3)}, ${hi.toFixed(3)}] +-${slack}`);
  }
  // and the off body really is quieter (the layer is what adds the life)
  const ranges = (t) => CORE.reduce((a, k) => a + range3(t.slice(0, 300).map((s) => s[k])), 0);
  assert(ranges(tr.on) > ranges(tr.off), 'the body layer adds no motion over the plain body');
});

check('Phase 3: no frame-to-frame snaps through anticipation, settle, walk start/stop, turns, gestures (15, 30, 60 fps)', () => {
  for (const fps of [15, 30, 60]) {
    const w = world(); const limit = MAX_PER_FRAME * 30 / fps * (fps === 15 ? 1.1 : 1.0) + (fps === 60 ? 0.03 : 0);
    let prev = null, worst = 0, worstK = '';
    const watch = (s) => { if (prev) { for (const k in s) { const d = Math.abs(s[k] - prev[k]); if (d > worst) { worst = d; worstK = k; } } } prev = s; };
    const run = stepper(w), dt = 1 / fps, n = (sec) => Math.round(sec * fps);
    const go = (sec) => { for (let i = 0; i < n(sec); i++) { run.step(dt); watch(snapshot(w.bones)); } };
    go(2);
    for (const g of ['p_bow', 'p_cheer', 'p_look_around', 'p_nod', 'p_shrug']) { w.api.play(g); go(PROC_LEN(w, g) + 0.6); }
    w.api.setActivity('reading'); go(1.5); w.api.setActivity('idle'); go(1.5);
    w.ctx.window.setLocomotion(true, 1, 300, 600); w.ctx.currentAnim = 'walk'; go(3);
    w.ctx.window.setLocomotion(true, -1, 300, 600); go(2);
    w.ctx.window.setLocomotion(false, 0, 0, 600); w.ctx.currentAnim = 'idle'; go(3);
    w.api.life.speaking = true; go(2); w.api.life.speaking = false; go(1);
    assert(worst <= limit, `${fps} fps: ${worstK} jumped ${worst.toFixed(3)} rad in one frame (limit ${limit.toFixed(3)})`);
  }
});
function PROC_LEN(w, g) { return w.api.PROC[g].dur; }

check('Phase 3: a sizeable move winds up the other way first, overshoots a little, and settles', () => {
  const dt = 1 / 30, ramp = (i) => 0.3 * smoothstep3(i / 12);          // a 0.4 s move, as the pose springs produce
  const trace = (ch, hold) => { const w = world(); const f = w.api.blFollow; for (let i = 0; i < 30; i++) f(ch, 0, dt);
    const tr = []; for (let i = 0; i < 60; i++) tr.push(f(ch, ramp(i), dt)); return tr; };
  const tr = trace('headY');
  assert(Math.min(...tr.slice(0, 8)) < -0.004, `no anticipation: the move never starts the other way (min ${Math.min(...tr.slice(0, 8)).toFixed(4)})`);
  assert(Math.min(...tr) > -0.04, 'the wind-up is too big');
  const over = Math.max(...tr) - 0.3;
  assert(over > 0.3 * 0.005 && over < 0.3 * 0.15, `overshoot ${(over / 0.3 * 100).toFixed(2)}% of the move, wanted 0.5-15%`);
  assert(Math.abs(tr[59] - 0.3) < 0.005, `did not settle (${tr[59].toFixed(4)})`);
  // a small tremor is no "move": no wind-up for it
  const w2 = world(); const f2 = w2.api.blFollow; for (let i = 0; i < 30; i++) f2('headY', 0, dt);
  let dip = 0; for (let i = 0; i < 60; i++) dip = Math.min(dip, f2('headY', 0.02 * Math.sin(i / 5), dt));
  assert(dip > -0.03, `a tiny motion triggered a big wind-up (${dip.toFixed(3)})`);
  // the head leads: it reaches 60% of a move before the spine does
  const h = world(); const hf = h.api.blFollow; for (let i = 0; i < 30; i++) { hf('headY', 0, dt); hf('spineY', 0, dt); }
  let th = -1, ts = -1;
  for (let i = 0; i < 60; i++) { const a = hf('headY', ramp(i), dt), b = hf('spineY', ramp(i), dt); if (th < 0 && a > 0.18) th = i; if (ts < 0 && b > 0.18) ts = i; }
  assert(th >= 0 && ts >= th, `the head does not lead the spine (head frame ${th}, spine frame ${ts})`);
  // a hard step (a gesture that starts at once) still never jumps: the follower smooths it
  const w3 = world(); const f3 = w3.api.blFollow; for (let i = 0; i < 30; i++) f3('spineX', 0, dt);
  let prevv = 0, worst = 0; for (let i = 0; i < 40; i++) { const y = f3('spineX', 0.4, dt); worst = Math.max(worst, Math.abs(y - prevv)); prevv = y; }
  assert(worst < 0.2, `a hard step moved ${worst.toFixed(3)} rad in one frame`);
});
function smoothstep3(x) { x = Math.max(0, Math.min(1, x)); return x * x * (3 - 2 * x); }

check('Phase 3: walking starts with a lean back and stops with a lean forward that settles', () => {
  const w = world(); w.ctx.window.setLocomotion(false, 0, 0, 600);
  const spine = [];
  const pc = w.api.pc; let t = 0; const clk = w.now();
  const step = () => { t += 1 / 30; const f = pc.step(t, 1 / 30); w.api.writePose(f, clk + t); return f; };
  for (let i = 0; i < 60; i++) step();
  const base = step().o.spineX;
  w.ctx.currentAnim = 'walk';
  let minStart = 0;
  for (let i = 0; i < 12; i++) minStart = Math.min(minStart, step().o.spineX - base);
  assert(minStart < -0.006, `no lean back before the walk (${minStart.toFixed(4)})`);
  for (let i = 0; i < 60; i++) step();
  const cruise = step().o.spineX;
  w.ctx.currentAnim = 'idle';
  let maxStop = -9, endv = 0;
  for (let i = 0; i < 12; i++) maxStop = Math.max(maxStop, step().o.spineX - cruise);
  for (let i = 0; i < 90; i++) endv = step().o.spineX;
  assert(maxStop > 0.006, `no lean forward as she stops (${maxStop.toFixed(4)})`);
  assert(Math.abs(endv - base) < 0.05, `the torso did not settle back after the walk (${endv.toFixed(3)} vs ${base.toFixed(3)})`);
});

check('Phase 3: breathing is scaled by energy: faster and deeper when lively, slower when tired', () => {
  const measure = (e) => {
    const w = world(); const aff = w.api.affect(); aff.e = aff.te = e;
    let wraps = 0, prevB = 0; const chest = [];
    for (let i = 0; i < 600; i++) {                                   // 20 s, controller + body only (no presence easing)
      w.frame(); const b = w.api.bl.breath; if (b < prevB) wraps++; prevB = b; if (i >= 60) chest.push(w.bones.chest.rotation.x);
    }
    return { rate: wraps / 20, depth: range3(chest) };
  };
  const tired = measure(0.1), lively = measure(0.95);
  assert(lively.rate > tired.rate * 1.4, `breath rate tired ${tired.rate} /s, lively ${lively.rate} /s`);
  assert(lively.depth > tired.depth * 1.15, `breath depth tired ${tired.depth.toFixed(4)}, lively ${lively.depth.toFixed(4)}`);
  assert(tired.rate > 0.12 && lively.rate < 0.5, 'breathing rate is not human');
  assert(lively.depth < 0.15, `breathing is too deep (${lively.depth.toFixed(3)} rad)`);
});

check('Phase 3: the noise never repeats over 60 s (no lag has a near-identical copy)', () => {
  for (const seed of [11, 4242, 987654]) {
    const w = world(); w.api.bl.seed = seed;
    for (let ci = 0; ci < w.api.BL_NOISE.length; ci++) {
      const f = w.api.BL_NOISE[ci][2], sig = [];
      for (let i = 0; i < 4800; i++) sig.push(w.api.bnoise(i / 30 * f, seed + ci * 131));       // 160 s at 30 Hz, 1 sample per frame
      const a = sig.slice(0, 1800);                                  // the first 60 s
      for (let lag = 150; lag <= 3000; lag += 30) {                  // 5 s .. 100 s later
        const c = corr3(a, sig.slice(lag, lag + 1800));
        assert(Math.abs(c) < 0.9, `seed ${seed} channel ${w.api.BL_NOISE[ci][0]}: lag ${lag / 30}s repeats (corr ${c.toFixed(2)})`);
      }
    }
  }
  // and the real body channel (no breathing in spine.y): the pose itself does not repeat either
  const w = world(); const sy = [];
  for (let i = 0; i < 4500; i++) { w.frame(); sy.push(w.bones.spine.rotation.y); }
  const a = sy.slice(0, 1800);
  for (let lag = 300; lag <= 2700; lag += 30) assert(Math.abs(corr3(a, sy.slice(lag, lag + 1800))) < 0.9, `spine.y repeats at ${lag / 30}s`);
});

check('Phase 3: the weight shifts between the feet: hips sway, pelvis rolls, the feet stay planted', () => {
  const w = world(); const hx = [], roll = [], slide = [];
  for (let i = 0; i < 1800; i++) {                                    // 60 s standing, controller + body only
    w.frame();
    const hip = w.bones.hips, ang = w.bones.leftUpperLeg.rotation.z, angR = w.bones.rightUpperLeg.rotation.z;
    hx.push(hip.position.x); roll.push(hip.rotation.z);
    // planar feet: hip joint +-0.09 m from the pelvis centre, legs 0.8 m, both rotated by the pelvis roll and their own z
    const footL = hip.position.x + 0.09 * Math.cos(hip.rotation.z) + 0.8 * Math.sin(hip.rotation.z + ang);
    const footR = hip.position.x - 0.09 * Math.cos(hip.rotation.z) + 0.8 * Math.sin(hip.rotation.z + angR);
    slide.push(Math.max(Math.abs(footL - 0.09), Math.abs(footR + 0.09)));
  }
  assert(range3(hx) > 0.008, `the hips never shift sideways (${range3(hx).toFixed(4)} m)`);
  assert(range3(roll) > 0.015, `the pelvis never rolls (${range3(roll).toFixed(4)} rad)`);
  assert(Math.max(...hx.map(Math.abs)) < 0.03 && Math.max(...roll.map(Math.abs)) < 0.08, 'the weight shift is too big');
  assert(Math.max(...slide) < 0.004, `a foot slides ${(Math.max(...slide) * 100).toFixed(2)} cm while she shifts her weight`);
  // it settles: after the shift target returns to 0 the hips come back to the middle
  w.api.bl.shiftTarget = 0; w.api.bl.nextShift = 1e9;
  for (let i = 0; i < 240; i++) w.frame();
  assert(Math.abs(w.bones.hips.position.x) < 0.002 && Math.abs(w.bones.hips.rotation.z) < 0.006, 'the hips did not settle back to the middle');
});

check('Phase 3: walking: hips sway over the stance foot, the pelvis turns, and the stance foot sweeps back evenly', () => {
  const w = world(); w.ctx.window.setLocomotion(true, 1, 300, 600); w.ctx.currentAnim = 'walk';
  for (let i = 0; i < 60; i++) w.frame();
  const hx = [], yaw = [];
  for (let i = 0; i < 120; i++) { w.frame(); hx.push(w.bones.hips.position.x); yaw.push(w.bones.hips.rotation.y); }
  assert(range3(hx) > 0.02, `no lateral hip sway while walking (${range3(hx).toFixed(4)} m)`);
  assert(range3(yaw) > 0.06, `no pelvic turn while walking (${range3(yaw).toFixed(3)} rad)`);
  // foot planting: the stride waveform is trapezoid-like: through the middle of the stance the foot's speed is steady
  const sp = w.api.stridePlant, N = 400;
  assert(Math.abs(sp(Math.PI / 2) - 1) < 1e-9 && Math.abs(sp(-Math.PI / 2) + 1) < 1e-9, 'the stride range changed');
  const slope = (ph) => (sp(ph + 1e-4) - sp(ph - 1e-4)) / 2e-4;
  const mid = [], midSin = [];
  for (let i = 0; i < N; i++) { const ph = Math.PI * (0.62 + 0.76 * i / N); mid.push(Math.abs(slope(ph))); midSin.push(Math.abs(Math.cos(ph))); }
  const evenness = (a) => Math.min(...a) / Math.max(...a);
  assert(evenness(mid) > 0.7, `the stance foot does not sweep evenly (min/max speed ${evenness(mid).toFixed(2)})`);
  assert(evenness(mid) > evenness(midSin) + 0.3, 'no better than a sine');
  for (let ph = 0; ph < 6.3; ph += 0.05) assert(Math.abs(sp(ph)) <= 1 + 1e-9, 'the stride overshoots its range');
});

check('Phase 3: turning to walk anticipates, overshoots a little and settles; flag off is the plain ease', () => {
  const w = world(); const dt = 1 / 30, tr = []; let face = 0;
  for (let i = 0; i < 20; i++) face = w.api.faceStep(face, 0, dt);
  for (let i = 0; i < 90; i++) { face = w.api.faceStep(face, 0.75, dt); tr.push(face); }
  assert(Math.min(...tr.slice(0, 5)) < -0.004, `no counter-turn before the turn (${Math.min(...tr.slice(0, 5)).toFixed(4)})`);
  const over = Math.max(...tr) - 0.75;
  assert(over > 0.75 * 0.01 && over < 0.75 * 0.15, `turn overshoot ${(over / 0.75 * 100).toFixed(1)}%`);
  assert(Math.abs(tr[89] - 0.75) < 0.005, `the turn did not settle (${tr[89].toFixed(4)})`);
  let worst = 0; for (let i = 1; i < tr.length; i++) worst = Math.max(worst, Math.abs(tr[i] - tr[i - 1]));
  assert(worst < 0.12, `the turn jumps ${worst.toFixed(3)} rad in one frame`);
  const off = world(); off.ctx.window.EVA_BODY_LIFE = false;
  const plain = off.api.faceStep(0.1, 0.75, dt);
  assert(Math.abs(plain - (0.1 + 0.65 * Math.min(1, dt * 4))) < 1e-12, 'flag off: the turn is not the old exponential ease');
});

check('Phase 3: focus mode keeps the body small and still (weight shift and noise shrink)', () => {
  const measure = (focus) => {
    const w = world(); const run = stepper(w);
    w.api.setWorld(worldPayload({ focus, keys: focus ? 300 : 0 }));
    const hx = [], sp = [];
    for (let i = 0; i < 1200; i++) { run.step(); hx.push(w.bones.hips.position.x); sp.push(w.bones.spine.rotation.y); }
    return { hx: range3(hx), sp: range3(sp) };
  };
  const calm = measure(true), free = measure(false);
  assert(free.hx > 0.006, `the control never shifts her weight (${free.hx.toFixed(4)})`);
  assert(calm.hx < free.hx * 0.7, `focus mode still sways: ${calm.hx.toFixed(4)} vs ${free.hx.toFixed(4)}`);
  assert(calm.sp < free.sp, `focus mode: the spine moves as much as when free (${calm.sp.toFixed(4)} vs ${free.sp.toFixed(4)})`);
});

// Spring-bone stand-ins: hair, skirt, bust. Same field names three-vrm's VRMSpringBoneJoint uses.
function springJoint(name, power, stiff, drag, dir = [0, -1, 0]) {
  return { bone: { name }, settings: { gravityDir: { x: dir[0], y: dir[1], z: dir[2], set(x, y, z) { this.x = x; this.y = y; this.z = z; } },
                                       gravityPower: power, stiffness: stiff, dragForce: drag, hitRadius: 0.01 } };
}
function clothWorld() {
  const w = world();
  const hair = springJoint('J_Sec_Hair1_01', 0, 0.85, 0.4), skirt = springJoint('J_Sec_L_SkirtBack_01', 0, 0.5, 0.05),
        coat = springJoint('J_Sec_L_CoatSkirtFront_01', 0, 0.75, 0.0), bust = springJoint('J_Sec_L_Bust1', 0, 0.75, 0.05);
  w.ctx.vrmInstance.springBoneManager = { joints: new Set([hair, skirt, coat, bust]) };
  return { w, hair, skirt, coat, bust };
}
const ang3 = (j) => j.settings.gravityDir;

check('Phase 3: hair and skirt feel her own acceleration (and a light breeze), the bust is left alone', () => {
  const { w, hair, skirt, bust } = clothWorld();
  const t0 = 100, dt = 1 / 30; let t = t0;
  const step = (n) => { for (let i = 0; i < n; i++) { t += dt; w.api.clothStep(dt, t); } };
  w.ctx.window.EVA_CLOTH_TUNE = { stiffness: 0.8, drag: 0.9, breeze: 0 };         // no breeze: only her own motion
  step(30);
  assert(Math.abs(ang3(hair).x) < 0.02 && hair.settings.gravityPower < 0.02, 'standing still with no breeze must leave the hair hanging');
  w.ctx.window.setLocomotion(true, 1, 400, 600);                                    // she sets off to the right
  let minX = 0, maxPow = 0;
  for (let i = 0; i < 20; i++) { step(1); minX = Math.min(minX, ang3(hair).x); maxPow = Math.max(maxPow, hair.settings.gravityPower); }
  assert(minX < -0.1, `hair does not lag behind when she sets off (gravityDir.x ${minX.toFixed(3)})`);
  step(60);
  assert(ang3(hair).x < -0.05 && hair.settings.gravityPower > 0.1, 'hair does not trail behind her while she walks');
  assert(skirt.settings.gravityPower < hair.settings.gravityPower, 'the skirt moves as much as the hair');
  w.ctx.window.setLocomotion(false, 0, 0, 600);                                      // she stops: the hair swings on
  let maxX = -9;
  for (let i = 0; i < 20; i++) { step(1); maxX = Math.max(maxX, ang3(hair).x); }
  assert(maxX > 0.0, `hair does not swing on when she stops (gravityDir.x max ${maxX.toFixed(3)})`);
  for (const j of [hair, skirt]) assert(j.settings.gravityPower <= 5, 'external force is not bounded');
  assert(Math.abs(hair.settings.stiffness - 0.85 * 0.8) < 1e-9 && Math.abs(hair.settings.dragForce - 0.4 * 0.9) < 1e-9, 'stiffness/drag tuning not applied');
  assert(bust.settings.gravityPower === 0 && bust.settings.stiffness === 0.75 && ang3(bust).x === 0 && ang3(bust).y === -1, 'the bust spring was touched');
});

check('Phase 3: a light breeze keeps hair stirring when she stands still, and never blows hard', () => {
  const { w, hair } = clothWorld();
  w.ctx.window.EVA_CLOTH_TUNE = { stiffness: 1, drag: 1, breeze: 1 };
  const xs = [], pw = []; const dt = 1 / 30;
  for (let i = 0; i < 3600; i++) { w.api.clothStep(dt, 50 + i * dt); xs.push(ang3(hair).x); pw.push(hair.settings.gravityPower); }
  assert(range3(pw) > 0.05, `the breeze never changes (power range ${range3(pw).toFixed(3)})`);
  assert(Math.max(...pw) < 0.7, `the breeze is not light (max ${Math.max(...pw).toFixed(2)} m/s^2)`);
  assert(Math.max(...xs) > 0.15 && Math.min(...xs) < -0.15, 'the breeze never changes direction');
  // per-frame change of the force is small: no flicker
  let worst = 0; for (let i = 1; i < pw.length; i++) worst = Math.max(worst, Math.abs(pw[i] - pw[i - 1]));
  assert(worst < 0.03, `the breeze jumps ${worst.toFixed(3)} in one frame`);
});

check('Phase 3: EVA_CLOTH_PHYSICS off restores every spring exactly; no spring bones falls back to spring-dampers', () => {
  const { w, hair, skirt, coat } = clothWorld(); const dt = 1 / 30;
  w.ctx.window.setLocomotion(true, 1, 400, 600);
  for (let i = 0; i < 90; i++) w.api.clothStep(dt, 10 + i * dt);
  assert(hair.settings.gravityPower > 0.05, 'the cloth was never driven');
  w.ctx.window.EVA_CLOTH_PHYSICS = false;
  w.api.clothStep(dt, 20);
  for (const [j, p, s, d] of [[hair, 0, 0.85, 0.4], [skirt, 0, 0.5, 0.05], [coat, 0, 0.75, 0]]) {
    assert(j.settings.gravityPower === p && j.settings.stiffness === s && j.settings.dragForce === d && ang3(j).x === 0 && ang3(j).y === -1,
           'a spring was not restored when cloth physics was switched off');
  }
  const off = clothWorld(); off.w.ctx.window.EVA_CLOTH_PHYSICS = false;
  off.w.ctx.window.setLocomotion(true, 1, 400, 600);
  for (let i = 0; i < 60; i++) off.w.api.clothStep(dt, 10 + i * dt);
  assert(off.hair.settings.gravityPower === 0 && off.hair.settings.stiffness === 0.85, 'flag off: the springs were touched');
  // a model without spring bones: spring-dampers on bones named hair/skirt, restored when off
  const fb = world(); const mk = (name) => ({ name, isBone: true, rotation: { x: 0.1, y: 0, z: 0.2 } });
  const bones = [mk('J_Sec_Hair1_01'), mk('J_Sec_L_SkirtBack_01'), mk('J_Adj_Bust'), mk('head')];
  fb.ctx.vrmInstance.scene.traverse = (fn) => bones.forEach(fn);
  fb.ctx.window.EVA_CLOTH_TUNE = { breeze: 0 };
  fb.ctx.window.setLocomotion(true, 1, 400, 600);
  let swing = 0;
  for (let i = 0; i < 90; i++) { fb.api.clothStep(dt, 10 + i * dt); swing = Math.max(swing, Math.abs(bones[0].rotation.z - 0.2)); }
  assert(swing > 0.01 && swing <= 0.3, `fallback hair does not swing (${swing.toFixed(3)})`);
  assert(bones[2].rotation.x === 0.1 && bones[3].rotation.z === 0.2, 'the fallback touched the bust or a body bone');
  fb.ctx.window.EVA_CLOTH_PHYSICS = false; fb.api.clothStep(dt, 20);
  assert(bones[0].rotation.x === 0.1 && bones[0].rotation.z === 0.2, 'the fallback bone was not restored');
});

check('Phase 3: with the flags off the body is exactly as before (numbers recorded from the pre-Phase-3 code)', () => {
  // idle 4 s, walk 3 s, idle 2 s: sums of every bone channel at four moments, from the code before Phase 3
  const GOLDEN = [14.62265421846105, 7.006579901962738, 10.30838068310994, 13.994092174137732];
  const run = () => {
    const w = world(); w.ctx.window.EVA_BODY_LIFE = false; w.ctx.window.EVA_CLOTH_PHYSICS = false; const out = [];
    const sum = () => { const s = snapshot(w.bones); return Object.keys(s).reduce((a, k, i) => a + s[k] * (1 + i * 0.37), 0); };
    w.api.setActivity('idle');
    for (let i = 0; i < 120; i++) w.frame(); out.push(sum());
    w.ctx.currentAnim = 'walk';
    for (let i = 0; i < 45; i++) w.frame(); out.push(sum());
    for (let i = 0; i < 45; i++) w.frame(); out.push(sum());
    w.ctx.currentAnim = 'idle';
    for (let i = 0; i < 60; i++) w.frame(); out.push(sum());
    return out;
  };
  const a = run(), b = run();
  for (let i = 0; i < 4; i++) {
    assert(Math.abs(a[i] - GOLDEN[i]) < 1e-9, `flags off, moment ${i}: ${a[i]} differs from the old body (${GOLDEN[i]})`);
    assert(a[i] === b[i], 'the old body is not deterministic');
  }
  const w = world(); w.ctx.window.EVA_BODY_LIFE = false; const f = w.frame();
  assert(!f.bl, 'flag off but hip/leg extras still produced');
  // and on, the same scenario is different (the flag does something)
  const on = world(); for (let i = 0; i < 120; i++) on.frame();
  const sOn = snapshot(on.bones); const sum = Object.keys(sOn).reduce((a2, k, i) => a2 + sOn[k] * (1 + i * 0.37), 0);
  assert(Math.abs(sum - GOLDEN[0]) > 1e-4, 'the flag changes nothing');
});

check('Phase 3: cheap: the body layer and the cloth cost a small fraction of a 60 fps frame', () => {
  const { w } = clothWorld();
  const joints = Array.from(w.ctx.vrmInstance.springBoneManager.joints);
  for (let i = 0; i < 30; i++) joints.push(springJoint('J_Sec_Hair1_0' + i, 0, 0.8, 0.4));
  w.ctx.vrmInstance.springBoneManager.joints = new Set(joints);
  const f = w.api.pc.step(1, 1 / 60);
  for (let i = 0; i < 200; i++) { w.api.bodyLifeApply(f, 1); w.api.clothStep(1 / 60, 1 + i / 60); }   // warm up
  const N = 6000, t0 = process.hrtime.bigint();
  for (let i = 0; i < N; i++) { f.t = i / 60; w.api.bodyLifeApply(f, 1); w.api.clothStep(1 / 60, 10 + i / 60); }
  const ms = Number(process.hrtime.bigint() - t0) / 1e6 / N;
  assert(ms < 0.25, `${ms.toFixed(3)} ms per frame for the body layer and cloth (budget 0.25 ms, a 60 fps frame is 16.7 ms)`);
});

// ---- Phase 4: music and dancing -------------------------------------------------------------
// The page gets {playing, bpm, phase, energy, low, high} ~20x a second; the click track below has a beat
// at every integer of (now - t0) * bpm / 60, so the phase it reports is what music.py would report.
// Phrases are drawn at random; a seeded generator keeps these checks repeatable (restored at the end).
const realRandom = Math.random;
(function () { let s = 20261114; Math.random = () => { s = (s + 0x6D2B79F5) | 0; let x = Math.imul(s ^ (s >>> 15), 1 | s);
  x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296; }; })();
const musicPayload = (over) => Object.assign({ playing: true, bpm: 120, phase: 0, energy: 0.7, low: 0.6, high: 0.4 }, over || {});
function dancer(over) {
  const w = world(); w.ctx.window.EVA_PRESENCE = true;
  vm.runInContext('this.api.setMusic = window.setMusic; this.api.dance = () => dance;', w.ctx);
  const run = stepper(w);
  w.api.setWorld(worldPayload(over && over.world));
  const t0 = w.now() + 0.37;                        // the first beat is not at the first frame
  let sendAt = 0;
  const d = {
    w, run, t0,
    play(seconds, o) {
      o = Object.assign({ bpm: 120, energy: 0.7, send: true }, over || {}, o || {});
      const frames = [];
      for (let i = 0, n = Math.round(seconds * 30); i < n; i++) {
        run.step();
        const now = run.now();
        if (o.send && now >= sendAt) {
          sendAt = now + 0.05;
          w.api.setMusic(musicPayload({ bpm: o.bpm, energy: o.energy, phase: (((now - t0) * o.bpm / 60) % 1 + 1) % 1,
                                        playing: o.playing !== false }));
        }
        frames.push({ now, snap: snapshot(w.bones) });
      }
      return frames;
    },
  };
  return d;
}
const rangeOf = (frames, key) => { const v = frames.map(f => f.snap[key]); return Math.max(...v) - Math.min(...v); };
const armRange = (frames) => Math.max(...['leftUpperArm.z', 'rightUpperArm.z', 'leftUpperArm.x', 'rightUpperArm.x',
  'leftLowerArm.y', 'rightLowerArm.y'].map(k => rangeOf(frames, k)));
function worstStep(frames) {
  let worst = 0;
  for (let i = 1; i < frames.length; i++) worst = Math.max(worst, maxStep(frames[i - 1].snap, frames[i].snap));
  return worst;
}

check('with music playing she moves on the beat: bounded, never snaps (Phase 4)', () => {
  const d = dancer();
  for (let i = 0; i < 30; i++) d.run.step();
  const idle = d.play(4, { send: false });
  const frames = d.play(14);
  const D = d.w.api.dance();
  assert(D.w > 0.9, `she did not start dancing (weight ${D.w.toFixed(2)})`);
  assert(worstStep(idle.concat(frames)) <= MAX_PER_FRAME, `dancing snapped: ${worstStep(idle.concat(frames)).toFixed(3)} rad in one frame`);
  for (const f of frames) {
    for (const k in f.snap) if (!/\.py$/.test(k)) assert(Math.abs(f.snap[k]) < 3.0, `${k}=${f.snap[k].toFixed(2)} out of range`);
    assert(Math.abs(f.snap['leftLowerArm.y']) < 2.75 && Math.abs(f.snap['rightLowerArm.y']) < 2.75, 'elbow over-bent');
    assert(Math.abs(f.snap['hips.py'] - 0.8) < 0.15, `hips left their range (${f.snap['hips.py'].toFixed(3)})`);
  }
  const late = frames.filter(f => f.now > d.t0 + 8);
  assert(rangeOf(late, 'hips.py') > 0.012, `no bounce: hips move only ${rangeOf(late, 'hips.py').toFixed(4)} m`);
  assert(rangeOf(idle, 'hips.py') < 0.006, 'she bounced with no music');
  // the knees give on the beat: folded over one beat, the hips are lowest at the beat (120 BPM = a beat every 0.5 s)
  const bins = Array.from({ length: 10 }, () => []);
  for (const f of late) bins[Math.floor((((f.now - d.t0) * 2) % 1) * 10)].push(f.snap['hips.py']);
  const avg = bins.map(b => b.reduce((a, c) => a + c, 0) / Math.max(1, b.length));
  const low = avg.indexOf(Math.min(...avg));
  let off = (low + 0.5) / 10; if (off > 0.5) off -= 1;                      // beats from the beat
  assert(Math.max(...avg) - Math.min(...avg) > 0.008, `the bounce does not follow the beat (${(Math.max(...avg) - Math.min(...avg)).toFixed(4)} m between beat and off-beat)`);
  assert(Math.abs(off) * 500 < 90, `the hips are lowest ${Math.round(off * 500)} ms from the beat`);
  assert(armRange(frames) > 0.2, `arms barely move (${armRange(frames).toFixed(2)} rad)`);
});

check('dance phrases last 8 or 16 beats, never repeat back to back, and vary (Phase 4)', () => {
  for (const [bpm, energy] of [[120, 0.7], [128, 0.95], [75, 0.3]]) {
    const d = dancer();
    d.play(110, { bpm, energy });
    const log = d.w.api.dance().log;
    assert(log.length >= 6, `${bpm} BPM: only ${log.length} phrases in 110 s`);
    for (let i = 0; i < log.length; i++) {
      assert(log[i].len === 8 || log[i].len === 16, `${bpm} BPM: a phrase of ${log[i].len} beats`);
      if (i) {
        assert(log[i].sig !== log[i - 1].sig, `${bpm} BPM: "${log[i].sig}" twice in a row`);
        assert(Math.abs((log[i].at - log[i - 1].at) - log[i - 1].len) < 1.0,
          `${bpm} BPM: phrase ${i - 1} ran ${(log[i].at - log[i - 1].at).toFixed(1)} beats, not ${log[i - 1].len}`);
      }
    }
    assert(new Set(log.map(p => p.sig)).size >= 4, `${bpm} BPM: only ${new Set(log.map(p => p.sig)).size} different phrases`);
    assert(new Set(log.map(p => p.sig.split('/')[0])).size >= 3, `${bpm} BPM: the arms use too few patterns`);
    if (bpm < 90) assert(log.every(p => p.tier === 'calm'), 'a slow song should get the calm sway');
  }
});

check('intensity follows loudness, and a slow quiet song is a calm sway (Phase 4)', () => {
  const measure = (bpm, energy) => {
    const d = dancer(); d.play(6, { bpm, energy });
    const late = d.play(12, { bpm, energy });
    return { hips: rangeOf(late, 'hips.py'), arms: armRange(late), worst: worstStep(late) };
  };
  const quiet = measure(120, 0.15), loud = measure(120, 1.0), slow = measure(72, 0.3);
  assert(loud.hips > quiet.hips * 1.3, `loud ${loud.hips.toFixed(4)} m vs quiet ${quiet.hips.toFixed(4)} m of bounce`);
  assert(slow.hips < loud.hips && slow.arms < loud.arms + 0.4, 'the slow song is not calmer than the loud one');
  for (const m of [quiet, loud, slow]) assert(m.worst <= MAX_PER_FRAME, `snapped ${m.worst.toFixed(3)}`);
});

check('she does not dance in focus mode, with no music, on stale or bad input, or when switched off (Phase 4)', () => {
  const still = (frames) => rangeOf(frames, 'hips.py') < 0.006;
  // focus mode: the same music, but the person is typing fast
  const f = dancer({ world: { focus: true, keys: 300 } });
  f.play(2);
  const fr = f.play(8);
  assert(f.w.api.dance().w < 0.01, `weight ${f.w.api.dance().w.toFixed(3)} in focus mode`);
  assert(still(fr), 'she bounced in focus mode');
  assert(f.w.api.dance().log.length === 0, 'she started a phrase in focus mode');
  // control: not in focus mode she does dance
  const c = dancer(); c.play(2); assert(!still(c.play(8)), 'the control never danced');
  // she is dancing and focus mode begins: she settles smoothly
  const s = dancer(); s.play(8);
  s.w.api.setWorld(worldPayload({ focus: true, keys: 300 }));
  const after = s.play(4);
  assert(s.w.api.dance().w < 0.05, `still dancing 4 s after focus began (${s.w.api.dance().w.toFixed(2)})`);
  assert(worstStep(after) <= MAX_PER_FRAME, 'stopping for focus snapped');
  // no music, and music that says it is not playing
  const n = dancer(); n.play(2, { send: false }); assert(still(n.play(6, { send: false })), 'danced with no music');
  const p = dancer(); p.play(2, { playing: false }); assert(still(p.play(6, { playing: false })), 'danced to playing:false');
  // music that stops being reported (Python died, the sound ended): she stops within 3 s
  const st = dancer(); st.play(8);
  const gone = st.play(6, { send: false });
  assert(st.w.api.dance().w < 0.01, 'she keeps dancing to music that stopped');
  assert(worstStep(gone) <= MAX_PER_FRAME, 'stopping snapped');
  // garbage in: ignored, never throws
  const g = dancer();
  for (const bad of [null, undefined, 5, 'x', {}, { playing: true, bpm: 'fast', phase: 'x' }, { playing: true, bpm: 5000, phase: 0 }])
    g.w.api.setMusic(bad);
  assert(still(g.play(4, { send: false })), 'bad input made her dance');
  // the switch
  const off = dancer(); off.w.ctx.window.EVA_MUSIC_DANCE = false; off.play(2); assert(still(off.play(6)), 'danced while switched off');
});

Math.random = realRandom;
// ---- Phase 2: drives and the utility selector ------------------------------------------------
// Runs the page for a long stretch at 30 fps with the world arriving at 8 Hz, and plays Python's part for the
// intents the page queues (a wander moves her, a sit sits her), so the page sees the consequences.
function simulate(w, seconds, script, opts) {
  opts = opts || {};
  const run = stepper(w);
  const frames = Math.round(seconds * 30), D = w.api.drives;
  const lo = {}, hi = {}, names = ['energy', 'curiosity', 'boredom', 'social', 'focusRespect'];
  names.forEach((n) => { lo[n] = 1; hi[n] = 0; });
  const out = { intents: [], worst: 0, lo, hi, worstAt: '' };
  let eva = (opts.eva || [850, 700, 1000, 1080]).slice(), sitting = false, last = null, prev = null;
  for (let i = 0; i < frames; i++) {
    const ts = i / 30;
    if (i % 4 === 0) { last = worldPayload(Object.assign({ eva: eva.slice() }, script(ts))); w.api.setWorld(last); }
    run.step();
    const raw = w.api.takeDriveIntents();
    if (raw) for (const it of JSON.parse(raw)) {
      out.intents.push({ at: ts, it, eva: eva.slice(), payload: last });
      if (it.kind === 'sit') { sitting = true; w.api.setBodyState({ sitting: true }); }
      else {
        const half = (eva[2] - eva[0]) / 2, x = Math.max(half, Math.min(1920 - half, it.x));
        eva = [x - half, eva[1], x + half, eva[3]];
        if (sitting) { sitting = false; w.api.setBodyState({ sitting: false }); }
      }
    }
    const s = snapshot(w.bones);
    if (prev) { const m = maxStep(prev, s); if (m > out.worst) { out.worst = m; out.worstAt = `t=${ts.toFixed(1)}s beh=${w.api.presence.beh && w.api.presence.beh.name}`; } }
    prev = s;
    for (const n of names) { lo[n] = Math.min(lo[n], D[n]); hi[n] = Math.max(hi[n], D[n]); }
  }
  out.names = () => w.api.presence.behLog.filter((e) => !e.failed).map((e) => e.name);
  return out;
}
const quietScript = () => ({ idle: 30, speed: 0, keys: 0 });
const gapPoint = (p, r) => Math.hypot(Math.max(r[0] - p[0], p[0] - r[2], 0), Math.max(r[1] - p[1], p[1] - r[3], 0));
const gapRect = (a, b) => Math.hypot(Math.max(b[0] - a[2], a[0] - b[2], 0), Math.max(b[1] - a[3], a[1] - b[3], 0));

check('drives rise and fall on their own, stay in [0,1], and never snap her pose (10 simulated minutes)', () => {
  const w = world();
  // a day in miniature: quiet, then typing, then new windows, then the person leaves and returns, then night
  const script = (t) => {
    if (t >= 300) w.api.drives.hourOverride = 23;
    if (t < 180) return { idle: 30 };
    if (t < 240) return { keys: 200, idle: 0, focus: true };
    if (t < 300) return { keys: 0, idle: 5, speed: (Math.floor(t / 4) % 3) * 200, new: { rect: [10, 300, 400, 500], at: Math.floor(t / 20) + 1, kind: 'window' } };
    if (t < 480) return { idle: 130 + (t - 300) };
    return { idle: 1 };
  };
  w.api.drives.hourOverride = 14;
  const r = simulate(w, 600, script);
  for (const n in r.lo) {
    assert(r.lo[n] >= 0 && r.hi[n] <= 1, `${n} left [0,1]: ${r.lo[n]}..${r.hi[n]}`);
    assert(r.hi[n] - r.lo[n] > 0.05, `${n} never moved on its own (${r.lo[n].toFixed(2)}..${r.hi[n].toFixed(2)})`);
  }
  assert(r.worst <= MAX_PER_FRAME, `a bone jumped ${r.worst.toFixed(3)} rad in one frame (${r.worstAt})`);
  assert(r.names().length > 40, `she barely did anything in ten minutes (${r.names().length})`);
});

check('no sequence of three behaviours repeats exactly in 10 minutes; each pick draws fresh parameters', () => {
  const w = world();
  w.api.drives.hourOverride = 14;
  const r = simulate(w, 600, quietScript);
  const n = r.names(), seen = new Map();
  for (let i = 2; i < n.length; i++) {
    const k = n[i - 2] + '>' + n[i - 1] + '>' + n[i];
    assert(!seen.has(k), `the sequence ${k} came twice (picks ${seen.get(k)} and ${i})`);
    seen.set(k, i);
  }
  const kinds = new Set(n);
  assert(kinds.size >= 8, `only ${kinds.size} different behaviours in ten minutes: ${[...kinds].join(', ')}`);
  const sigs = {};
  for (const e of w.api.presence.behLog) (sigs[e.name] = sigs[e.name] || []).push(JSON.stringify(e.pr, (k, v) => typeof v === 'number' ? +v.toFixed(4) : v));
  let repeated = 0;
  for (const name in sigs) if (sigs[name].length >= 2) {
    repeated++;
    assert(new Set(sigs[name]).size === sigs[name].length, `two ${name} behaviours came with identical parameters`);
  }
  assert(repeated >= 5, 'too few behaviours came twice to judge their parameters');
  // utility, not a script: with the same world a tired night differs from a bright afternoon
  const count = (hour, name) => { let total = 0;
    for (let k = 0; k < 6; k++) { const x = world(); x.api.drives.hourOverride = hour;
      x.api.drives.energy = hour === 3 ? 0.15 : 0.9; total += simulate(x, 300, quietScript).names().filter((m) => m === name).length; }
    return total; };
  const yawnsNight = count(3, 'yawn'), yawnsDay = count(14, 'yawn');
  assert(yawnsNight > 1.3 * yawnsDay, `she yawns no more at 3 am (${yawnsNight}) than at 2 pm (${yawnsDay})`);
});

check('she waves when the person comes back after being away', () => {
  const w = world();
  const script = (t) => (t < 130 ? { idle: 30 + t } : { idle: Math.max(0, t - 131) });
  const r = simulate(w, 150, script);
  const wave = w.api.presence.behLog.find((e) => e.name === 'wave_return');
  assert(wave, 'no wave when the person came back');
  assert(wave.at - w.api.drives.returnedAt < 15 && wave.at >= w.api.drives.returnedAt, 'the wave was not tied to the return');
  const early = w.api.presence.behLog.filter((e) => e.name === 'wave_return' && e.at < 100);
  assert(early.length === 0, 'she waved although nobody had been away');
});

check('focus mode: only a corner settle and standing still, and nothing that moves the person\'s way', () => {
  const w = world();
  const r = simulate(w, 180, () => ({ focus: true, keys: 300, idle: 0 }));
  const names = r.names();
  assert(names.length > 0, 'she chose nothing at all in focus mode');
  for (const n of names) assert(n === 'corner_settle' || n === 'stay_still', `${n} in focus mode`);
  assert(r.intents.length <= 1, `${r.intents.length} moves in focus mode`);
  for (const i of r.intents) assert(i.it.kind === 'corner', `intent ${i.it.kind} in focus mode`);
  if (r.intents[0]) {
    const half = 75, x = r.intents[0].it.x;
    assert(x - half < 60 || x + half > 1920 - 60, `settled at x=${x}, not in a corner`);
  }
  const head = [], torso = [];
  const w2 = world(); const run = stepper(w2);
  for (let i = 0; i < 600; i++) { if (i % 4 === 0) w2.api.setWorld(worldPayload({ focus: true, keys: 300 })); run.step();
    head.push(w2.api.presence.headYaw); torso.push(w2.api.presence.torsoYaw); }
  assert(Math.max(...head) - Math.min(...head) < 3 && Math.max(...torso) - Math.min(...torso) < 1, 'she moved in focus mode');
  // mid-lean when the person starts concentrating: she eases out, nothing new but stillness starts, no snap
  const m = world(); m.api.drives.hourOverride = 14; const mrun = stepper(m);
  const wp = (focus) => worldPayload(focus ? { focus: true, keys: 300 } : { idle: 30 });
  let i = 0, switched = -1, prev = null, worst = 0;
  for (; i < 15000 && switched < 0; i++) {
    if (i % 4 === 0) m.api.setWorld(wp(false)); mrun.step();
    const b = m.api.presence.beh; if (b && b.kind === 'pose' && !b.still && i > 60) switched = i;
  }
  assert(switched > 0, 'never found a posture behaviour to interrupt');
  const startLog = m.api.presence.behLog.length;
  prev = snapshot(m.bones);
  for (let j = 0; j < 30 * 90; j++) {
    if (j % 4 === 0) m.api.setWorld(wp(true)); mrun.step();
    const s = snapshot(m.bones); worst = Math.max(worst, maxStep(prev, s)); prev = s;
  }
  const after = m.api.presence.behLog.slice(startLog).map((e) => e.name);
  for (const n of after) assert(n === 'corner_settle' || n === 'stay_still', `${n} started after focus began`);
  assert(worst <= MAX_PER_FRAME, `easing out of a lean snapped ${worst.toFixed(3)} rad`);
});

check('she keeps clear: no move ends or passes within 150 px of the cursor or the active window; leans go away from the cursor', () => {
  const w = world();
  w.api.drives.hourOverride = 14;
  // the cursor roams the whole screen, often right beside her; the window sits in the middle
  let cx = 960, cy = 540, seed = 7;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const script = (t) => { if (Math.floor(t * 8) !== Math.floor((t - 0.125) * 8)) { cx = 100 + rand() * 1720; cy = 300 + rand() * 780; }
    return { cursor: [Math.round(cx), Math.round(cy)], idle: 30, speed: 0, window: { rect: [500, 200, 1100, 800], kind: 'editor', title: 'x' } }; };
  const r = simulate(w, 600, script, { eva: [1500, 700, 1650, 1080] });
  assert(r.intents.length >= 5, `too few moves to judge (${r.intents.length})`);
  for (const { it, eva, payload } of r.intents) {
    if (it.kind === 'sit') continue;
    const half = (eva[2] - eva[0]) / 2, x = Math.max(half, Math.min(1920 - half, it.x));
    const end = [x - half, eva[1], x + half, eva[3]];
    const sweep = [Math.min(eva[0], end[0]), eva[1], Math.max(eva[2], end[2]), eva[3]];
    const win = payload.window.rect;
    for (const [name, gap] of [['cursor', (rc) => gapPoint(payload.cursor, rc)], ['window', (rc) => gapRect(rc, win)]]) {
      assert(gap(end) >= 150, `she ended ${gap(end).toFixed(0)} px from the ${name}`);
      assert(gap(sweep) >= Math.min(150, gap(eva)) - 1, `her path closed in on the ${name} (${gap(sweep).toFixed(0)} px, started ${gap(eva).toFixed(0)})`);
    }
  }
  // direct: a cursor close on her right makes every lean (and no peek) go to her left
  const t = world(); t.api.drives.hourOverride = 14;
  simulate(t, 900, () => ({ cursor: [1100, 900], idle: 30, window: { rect: [100, 100, 700, 700], kind: 'editor', title: 'x' } }));
  const leans = t.api.presence.behLog.filter((m) => m.name === 'lean');
  assert(leans.length >= 2, 'no leans to judge');
  for (const l of leans) assert(l.pr.side === -1, 'she leaned toward the cursor');
});

check('drives off: no behaviours, no intents, nothing changes (EVA_DRIVES = false)', () => {
  const w = world(); w.ctx.window.EVA_DRIVES = false;
  const r = simulate(w, 120, quietScript);
  assert(r.names().length === 0 && r.intents.length === 0, 'she kept choosing behaviours with the flag off');
});

console.log(failures ? `\n${failures} motion check(s) FAILED` : '\nall motion checks passed');
process.exit(failures ? 1 : 0);
