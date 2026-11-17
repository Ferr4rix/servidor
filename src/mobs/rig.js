// Esqueleto dos mobs: poses e caixas de colisão (porte de engine/mob_rig.py).
// O servidor sabe onde estão o corpo, a espada e o escudo em cada instante da
// animação. Matrizes 4x4 em arrays de 16 (linha a linha).
const fs = require("fs");

// ------------------------------------------------------------- matemática
function mat4() { return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; }

function mul(a, b) {
  const o = new Array(16).fill(0);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[i * 4 + k] * b[k * 4 + j];
    o[i * 4 + j] = s;
  }
  return o;
}

function apply(m, p) {
  return [0, 1, 2].map((i) => m[i * 4] * p[0] + m[i * 4 + 1] * p[1] + m[i * 4 + 2] * p[2] + m[i * 4 + 3]);
}

function invertRigid(m) {
  // inversa geral 4x4 (Gauss-Jordan), as matrizes são pequenas e poucas
  const a = m.slice();
  const inv = mat4();
  for (let c = 0; c < 4; c++) {
    let p = c;
    for (let r = c + 1; r < 4; r++) if (Math.abs(a[r * 4 + c]) > Math.abs(a[p * 4 + c])) p = r;
    if (p !== c) for (let k = 0; k < 4; k++) {
      [a[c * 4 + k], a[p * 4 + k]] = [a[p * 4 + k], a[c * 4 + k]];
      [inv[c * 4 + k], inv[p * 4 + k]] = [inv[p * 4 + k], inv[c * 4 + k]];
    }
    const d = a[c * 4 + c] || 1e-12;
    for (let k = 0; k < 4; k++) { a[c * 4 + k] /= d; inv[c * 4 + k] /= d; }
    for (let r = 0; r < 4; r++) if (r !== c) {
      const f = a[r * 4 + c];
      for (let k = 0; k < 4; k++) { a[r * 4 + k] -= f * a[c * 4 + k]; inv[r * 4 + k] -= f * inv[c * 4 + k]; }
    }
  }
  return inv;
}

function trs(t, q, s) {
  let [x, y, z, w] = q;
  const n = Math.hypot(x, y, z, w) || 1;
  x /= n; y /= n; z /= n; w /= n;
  const r = [
    1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
    2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
    2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
  ];
  return [
    r[0] * s[0], r[1] * s[1], r[2] * s[2], t[0],
    r[3] * s[0], r[4] * s[1], r[5] * s[2], t[1],
    r[6] * s[0], r[7] * s[1], r[8] * s[2], t[2],
    0, 0, 0, 1,
  ];
}

function slerp(a, b, t) {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  if (dot < 0) { b = b.map((v) => -v); dot = -dot; }
  if (dot > 0.9995) {
    const q = a.map((v, i) => v + (b[i] - v) * t);
    const n = Math.hypot(...q) || 1;
    return q.map((v) => v / n);
  }
  const th = Math.acos(Math.min(1, dot));
  const s = Math.sin(th);
  return a.map((v, i) => (Math.sin((1 - t) * th) * v + Math.sin(t * th) * b[i]) / s);
}

const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

// Caixa = { c: [x,y,z], a: [[eixo1],[eixo2],[eixo3]], h: [h1,h2,h3] }
function boxesIntersect(A, B, eps = 1e-6) {
  const t = sub3(B.c, A.c);
  const axes = [...A.a, ...B.a];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    const c = cross3(A.a[i], B.a[j]);
    const n = Math.hypot(...c);
    if (n > eps) axes.push(c.map((v) => v / n));
  }
  for (const ax of axes) {
    let ra = 0, rb = 0;
    for (let i = 0; i < 3; i++) {
      ra += A.h[i] * Math.abs(dot3(A.a[i], ax));
      rb += B.h[i] * Math.abs(dot3(B.a[i], ax));
    }
    if (Math.abs(dot3(t, ax)) > ra + rb + eps) return false;
  }
  return true;
}

function aabbBox(lo, hi) {
  return { c: lo.map((v, i) => (v + hi[i]) / 2), a: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
           h: lo.map((v, i) => (hi[i] - v) / 2) };
}

function segmentBoxT(p0, p1, box) {
  const d0 = box.a.map((ax) => dot3(ax, sub3(p0, box.c)));
  const d1 = box.a.map((ax) => dot3(ax, sub3(p1, box.c)));
  let t0 = 0, t1 = 1;
  for (let i = 0; i < 3; i++) {
    const d = d1[i] - d0[i];
    if (Math.abs(d) < 1e-9) { if (Math.abs(d0[i]) > box.h[i]) return null; continue; }
    let a = (-box.h[i] - d0[i]) / d, b = (box.h[i] - d0[i]) / d;
    if (a > b) [a, b] = [b, a];
    t0 = Math.max(t0, a); t1 = Math.min(t1, b);
    if (t0 > t1) return null;
  }
  return t0;
}

// glTF (Y para cima, frente -Z) -> mundo do jogo, girado por yaw (graus).
function placement(pos, yawDeg, scale) {
  const a = (yawDeg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  // Ry(yaw) * diag(1,1,-1) * scale
  return [
    c * scale, 0, -s * scale, pos[0],
    0, scale, 0, pos[1],
    -s * scale, 0, -c * scale, pos[2],
    0, 0, 0, 1,
  ];
}

function boxFrom(lo, hi, m) {
  const mid = lo.map((v, i) => (v + hi[i]) / 2);
  const c = apply(m, mid);
  const a = [], h = [];
  for (let i = 0; i < 3; i++) {
    const half = (hi[i] - lo[i]) / 2;
    const v = [m[i], m[4 + i], m[8 + i]].map((x) => x * half);
    const n = Math.hypot(...v);
    a.push(n > 1e-12 ? v.map((x) => x / n) : [0, 1, 2].map((k) => (k === i ? 1 : 0)));
    h.push(n);
  }
  return { c, a, h };
}

// ------------------------------------------------------------- rig
const CACHE = new Map();

class Rig {
  static load(file) {
    if (!CACHE.has(file)) CACHE.set(file, new Rig(file));
    return CACHE.get(file);
  }

  constructor(file) {
    const doc = JSON.parse(fs.readFileSync(file, "utf8"));
    this.doc = doc;
    this.bufs = (doc.buffers || []).map((b) => Buffer.from(b.uri.split(",", 2)[1], "base64"));
    const nodes = doc.nodes || [];
    this.names = nodes.map((n, i) => n.name || `node${i}`);
    this.parent = nodes.map(() => -1);
    nodes.forEach((n, i) => (n.children || []).forEach((c) => { this.parent[c] = i; }));
    this.order = [];
    const seen = new Set();
    const visit = (i) => {
      if (seen.has(i)) return;
      seen.add(i);
      if (this.parent[i] >= 0) visit(this.parent[i]);
      this.order.push(i);
    };
    nodes.forEach((_, i) => visit(i));
    this.rest = nodes.map((n) => [n.translation || [0, 0, 0], n.rotation || [0, 0, 0, 1], n.scale || [1, 1, 1]]);
    this.bounds = new Map();
    nodes.forEach((n, i) => { const b = (n.extras || {}).bounds; if (b) this.bounds.set(i, b); });
    this.clips = {};
    for (const anim of doc.animations || []) {
      const chans = new Map();
      let dur = 0;
      for (const ch of anim.channels || []) {
        const node = ch.target && ch.target.node;
        const p = ch.target && ch.target.path;
        if (node === undefined || !["translation", "rotation", "scale"].includes(p)) continue;
        const smp = anim.samplers[ch.sampler];
        const times = this._acc(smp.input).map((v) => v[0]);
        const vals = this._acc(smp.output);
        dur = Math.max(dur, times[times.length - 1]);
        if (!chans.has(node)) chans.set(node, {});
        chans.get(node)[p] = [times, vals];
      }
      const ex = anim.extras || {};
      this.clips[anim.name] = { chans, duration: ex.length || dur, loop: ex.loop || "once" };
    }
    this._sub = new Map();
  }

  _acc(i) {
    const a = this.doc.accessors[i];
    const v = this.doc.bufferViews[a.bufferView];
    const w = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type];
    const buf = this.bufs[v.buffer || 0];
    const off = (v.byteOffset || 0) + (a.byteOffset || 0);
    const out = [];
    for (let k = 0; k < a.count; k++) {
      const row = [];
      for (let j = 0; j < w; j++) row.push(buf.readFloatLE(off + (k * w + j) * 4));
      out.push(row);
    }
    return out;
  }

  index(name) { return this.names.indexOf(name); }

  clipTime(clip, t) {
    const c = this.clips[clip];
    if (!c || c.duration <= 0) return 0;
    if (c.loop === "loop") return ((t % c.duration) + c.duration) % c.duration;
    return Math.min(Math.max(t, 0), c.duration);
  }

  needed(bones) {
    const out = new Set();
    for (let i of bones) {
      while (i >= 0 && !out.has(i)) { out.add(i); i = this.parent[i]; }
    }
    return out;
  }

  // Com "nodes" (de needed) só calcula esses nós (os outros ficam undefined).
  world(clip, t = 0, nodes = null) {
    const c = this.clips[clip];
    const chans = c ? c.chans : new Map();
    t = c ? this.clipTime(clip, t) : 0;
    const out = new Array(this.names.length);
    for (const i of this.order) {
      if (nodes && !nodes.has(i)) continue;
      let [tr, rot, sc] = this.rest[i];
      const ch = chans.get(i) || {};
      for (const [p, [times, vals]] of Object.entries(ch)) {
        const v = sample(times, vals, t, p);
        if (p === "translation") tr = v; else if (p === "rotation") rot = v; else sc = v;
      }
      const local = trs(tr, rot, sc);
      out[i] = this.parent[i] >= 0 ? mul(out[this.parent[i]], local) : local;
    }
    return out;
  }

  subtreeBounds(bone, exclude = []) {
    const k = bone + "|" + exclude.join(",");
    if (this._sub.has(k)) return this._sub.get(k);
    const root = this.index(bone);
    const skip = new Set(exclude.map((n) => this.index(n)).filter((i) => i >= 0));
    const mats = this.world(null);
    const inv = invertRigid(mats[root]);
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    let any = false;
    for (const [i, [blo, bhi]] of this.bounds) {
      let j = i, inside = false;
      while (j >= 0) {
        if (skip.has(j)) break;
        if (j === root) { inside = true; break; }
        j = this.parent[j];
      }
      if (!inside) continue;
      const m = mul(inv, mats[i]);
      for (let c = 0; c < 8; c++) {
        const p = apply(m, [c & 1 ? bhi[0] : blo[0], c & 2 ? bhi[1] : blo[1], c & 4 ? bhi[2] : blo[2]]);
        for (let a = 0; a < 3; a++) { lo[a] = Math.min(lo[a], p[a]); hi[a] = Math.max(hi[a], p[a]); }
        any = true;
      }
    }
    const res = any ? [lo, hi] : null;
    this._sub.set(k, res);
    return res;
  }
}

function sample(times, vals, t, p) {
  const n = times.length;
  if (t <= times[0]) return vals[0];
  if (t >= times[n - 1]) return vals[n - 1];
  let hi = 1;
  while (hi < n && times[hi] <= t) hi++;
  const lo = hi - 1;
  const f = (t - times[lo]) / (times[hi] - times[lo]);
  if (p === "rotation") return slerp(vals[lo], vals[hi], f);
  return vals[lo].map((v, i) => v * (1 - f) + vals[hi][i] * f);
}

class MobBoxes {
  constructor(rig, hitboxes, scale) {
    this.rig = rig;
    this.scale = scale;
    this.defs = {};
    for (const [name, spec] of Object.entries(hitboxes || {})) {
      if (rig.index(spec.bone) < 0) {
        console.warn(`[mob] caixa ${name}: osso ${spec.bone} não existe`);
        continue;
      }
      const b = rig.subtreeBounds(spec.bone, spec.exclude || []);
      if (!b) continue;
      const pad = (spec.pad || 0) / 16;
      this.defs[name] = [rig.index(spec.bone), b[0].map((v) => v - pad), b[1].map((v) => v + pad)];
    }
  }

  boxes(pos, yaw, clip, t, only = null) {
    const bones = Object.entries(this.defs).filter(([n]) => !only || only.includes(n)).map(([, d]) => d[0]);
    const mats = this.rig.world(clip, t, this.rig.needed(bones));
    const place = placement(pos, yaw, this.scale);
    const out = {};
    for (const [name, [bone, lo, hi]] of Object.entries(this.defs)) {
      if (only && !only.includes(name)) continue;
      out[name] = boxFrom(lo, hi, mul(place, mats[bone]));
    }
    return out;
  }
}

module.exports = { Rig, MobBoxes, boxesIntersect, aabbBox, segmentBoxT, placement };
