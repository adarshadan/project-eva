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
                 setWorld: window.setWorld, presence, acknowledge: window.acknowledge,
                 affect: () => affect, drives, takeDriveIntents: window.takeDriveIntents,
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
