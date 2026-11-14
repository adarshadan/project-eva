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
console.log(failures ? `\n${failures} motion check(s) FAILED` : '\nall motion checks passed');
process.exit(failures ? 1 : 0);
