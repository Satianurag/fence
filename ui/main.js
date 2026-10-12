// Fence dashboard. Plain ES module, no dependencies.
// Every number on screen is read from data/fence.json (built by build_data.py from the real git history
// of entireio/cli, Fence's resolver output and the run records under runs/). The replay only animates
// through that recorded evidence and ends on the verdict and action the recorded run reached.

const $ = (id) => document.getElementById(id);
const FONT = '"Plex", ui-monospace, monospace';
const AMBER = "255,154,60";
const TEAL = [0.3, 0.82, 0.74];
const STEP_SECONDS = 3.2; // replay: one evidence commit per 3.2 s
const HOLD_STEPS = 3; // pause on the last step before looping

// ---------- small maths ----------
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const thousands = (n) => Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash(a, b = 0) {
  let h = Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

// column-major 4x4 matrices
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), r = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * r, -1, 0, 0, 2 * far * near * r, 0]);
}
function lookAt(eye, target, up = [0, 1, 0]) {
  const z = unit(sub(eye, target)), x = unit(cross(up, z)), y = cross(z, x);
  return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
}
function mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++)
    o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  return o;
}
function rotYX(yaw, pitch) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cx = Math.cos(pitch), sx = Math.sin(pitch);
  return new Float32Array([cy, sx * sy, -cx * sy, 0, 0, cx, sx, 0, sy, -sx * cy, cx * cy, 0, 0, 0, 0, 1]);
}
function project(m, p, w, h) {
  const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
  const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
  const ww = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
  if (ww <= 0.01) return null;
  return [(x / ww * 0.5 + 0.5) * w, (0.5 - y / ww * 0.5) * h, ww];
}

// ---------- canvas helpers ----------
function fit(canvas, maxDpr = 2) {
  const dpr = Math.min(window.devicePixelRatio || 1, maxDpr);
  const r = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  return { w: r.width, h: r.height, dpr };
}
function panel2d(canvas) {
  const ctx = canvas.getContext("2d");
  return () => {
    const { w, h, dpr } = fit(canvas);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = `8px ${FONT}`;
    ctx.textBaseline = "middle";
    return { ctx, w, h };
  };
}
function label(ctx, text, x, y, align = "left", color = "rgba(255,255,255,0.4)") {
  ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(text, x, y); ctx.textAlign = "left";
}
// dot painter that batches squares by alpha, so thousands of stipple dots cost a handful of fills
function stippler(levels = 8) {
  const bins = Array.from({ length: levels }, () => []);
  return {
    add(x, y, size, alpha) { bins[clamp(Math.floor(alpha * levels), 0, levels - 1)].push(x, y, size); },
    flush(ctx, color = "255,255,255") {
      bins.forEach((b, i) => {
        if (!b.length) return;
        ctx.fillStyle = `rgba(${color},${(i + 0.5) / levels})`;
        ctx.beginPath();
        for (let k = 0; k < b.length; k += 3) ctx.rect(b[k], b[k + 1], b[k + 2], b[k + 2]);
        ctx.fill();
        b.length = 0;
      });
    },
  };
}

// ---------- WebGL helpers ----------
function program(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  for (let i = 0, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i < n; i++) {
    const name = gl.getActiveUniform(p, i).name;
    u[name] = gl.getUniformLocation(p, name);
  }
  return { p, u };
}
function vao(gl, prog, attrs) {
  const v = gl.createVertexArray();
  gl.bindVertexArray(v);
  for (const [name, { data, size }] of Object.entries(attrs)) {
    const loc = gl.getAttribLocation(prog.p, name);
    if (loc < 0) continue;
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }
  gl.bindVertexArray(null);
  return v;
}

const POINT_VS = `#version 300 es
uniform mat4 uVP;
uniform float uTime, uFocus, uAperture, uDpr, uGain, uScan, uReal;
in vec4 aPos;   // xyz + luminance
in vec3 aCol;   // tint used in TRACED mode
in float aSeed;
out float vA;
out float vRing;
out vec3 vCol;
void main() {
  vec4 c = uVP * vec4(aPos.xyz, 1.0);
  gl_Position = c;
  float coc = min(abs(c.w - uFocus) * uAperture, 30.0);      // circle of confusion, px
  float base = 1.0 + aSeed * 1.3;
  float size = base + coc;
  gl_PointSize = size * uDpr;
  float energy = (base * base) / (size * size);              // blur spreads light, it does not add any
  float twinkle = 0.82 + 0.18 * sin(uTime * (0.7 + aSeed * 2.5) + aSeed * 61.0);
  float scan = 1.0 + 2.2 * exp(-pow((aPos.y - uScan) * 5.0, 2.0)) * step(0.05, aPos.y);
  float fog = exp(-max(c.w - 40.0, 0.0) * 0.03);
  vA = aPos.w * energy * twinkle * scan * fog * uGain;
  vRing = smoothstep(5.0, 15.0, coc);
  vCol = mix(vec3(1.0), aCol, uReal);
}`;
const POINT_FS = `#version 300 es
precision highp float;
in float vA;
in float vRing;
in vec3 vCol;
out vec4 o;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r = length(c);
  if (r > 1.0) discard;
  float disc = 1.0 - smoothstep(0.65, 1.0, r);
  float ring = smoothstep(0.55, 0.86, r) * (1.0 - smoothstep(0.88, 1.0, r));
  float a = mix(disc, disc * 0.22 + ring * 1.5, vRing);      // far out of focus a point becomes a ring
  o = vec4(vCol * (a * vA), 1.0);
}`;
const LINE_VS = `#version 300 es
uniform mat4 uVP;
uniform vec3 uCam;
uniform float uScan, uScanAmount;
in vec3 aPos;
in float aA;
out float vA;
void main() {
  gl_Position = uVP * vec4(aPos, 1.0);
  float d = length(aPos - uCam);
  float fog = exp(-max(d - 30.0, 0.0) * 0.035);
  float scan = 1.0 + uScanAmount * exp(-pow((aPos.y - uScan) * 2.5, 2.0));
  vA = aA * fog * scan;
}`;
const LINE_FS = `#version 300 es
precision highp float;
uniform vec3 uColor;
in float vA;
out vec4 o;
void main() { o = vec4(uColor * vA, 1.0); }`;
const GLOBE_VS = `#version 300 es
uniform mat4 uVP, uModel;
uniform vec3 uCam;
uniform float uDpr;
in vec4 aPos;   // xyz + type (0 surface, 1 graticule/arc, 2 hub)
in float aT;    // position along an arc, -1 when not on one
out float vA;
out float vType;
out float vT;
void main() {
  vec4 wp = uModel * vec4(aPos.xyz, 1.0);
  gl_Position = uVP * wp;
  float facing = dot(normalize(wp.xyz), normalize(uCam - wp.xyz));
  float front = smoothstep(-0.05, 0.12, facing);
  float rim = 1.0 - clamp(facing, 0.0, 1.0);
  float type = aPos.w;
  if (type < 0.5)      { vA = mix(0.05, 0.35 + 0.45 * rim, front); gl_PointSize = 1.3 * uDpr; }
  else if (type < 1.5) { vA = mix(0.03, 0.2, front);               gl_PointSize = 1.0 * uDpr; }
  else                 { vA = mix(0.25, 1.0, front);               gl_PointSize = 2.6 * uDpr; }
  vType = type;
  vT = aT;
}`;
const GLOBE_FS = `#version 300 es
precision highp float;
uniform float uTime, uLines;
in float vA;
in float vType;
in float vT;
out vec4 o;
void main() {
  float a = vA;
  if (uLines < 0.5) {
    vec2 c = gl_PointCoord * 2.0 - 1.0;
    a *= 1.0 - smoothstep(0.5, 1.0, length(c));
  }
  vec3 col = vType > 1.5 ? vec3(1.0, 0.6, 0.24) : vec3(1.0);
  if (vT >= 0.0) {                                            // arc: a pulse runs from hub to node
    float head = fract(uTime * 0.25);
    float d = fract(head - vT);
    a = 0.12 + 0.9 * exp(-d * 9.0);
    col = vec3(1.0, 0.6, 0.24);
  }
  o = vec4(col * a, 1.0);
}`;

// ---------- data ----------
const D = await fetch("data/fence.json").then((r) => r.json());
// ?issue=N picks which recorded run is replayed; the selector in the viewport header sets it
const QUERY = new URLSearchParams(location.search);
const REPLAY = D.replays[QUERY.get("issue")] || D.replays[String(D.issue.number)];
Object.assign(D, { issue: REPLAY.issue, path: REPLAY.path, spans: REPLAY.spans, agents: REPLAY.agents });
const RUN = REPLAY.fence, BASE = REPLAY.baseline;
const [heightBuf, coverBuf] = await Promise.all(["data/fence-terrain.bin", "data/fence-cover.bin"].map((f) => fetch(f).then((r) => r.arrayBuffer())));
const HEIGHTS = new Int16Array(heightBuf), COVER = new Uint8Array(coverBuf);
const { w: GW, h: GH } = D.grid;
const CELL = 0.24, SX = GW * CELL / 2, SZ = GH * CELL / 2, VERT = 7; // world extents and vertical scale
const MAXH = Math.max(1, HEIGHTS.reduce((m, v) => Math.max(m, v), 0));
const PATH = D.path, N = PATH.length;
const LINES_TOTAL = PATH.reduce((s, p) => s + p.lines, 0) || 1;

function sampleGrid(arr, wx, wz) {
  const gx = clamp(wx / CELL + GW / 2 - 0.5, 0, GW - 1.001), gz = clamp(wz / CELL + GH / 2 - 0.5, 0, GH - 1.001);
  const x0 = Math.floor(gx), z0 = Math.floor(gz), fx = gx - x0, fz = gz - z0, i = z0 * GW + x0;
  return (arr[i] * (1 - fx) + arr[i + 1] * fx) * (1 - fz) + (arr[i + GW] * (1 - fx) + arr[i + GW + 1] * fx) * fz;
}
const heightAt = (wx, wz) => (sampleGrid(HEIGHTS, wx, wz) / MAXH) ** 0.6 * VERT;
const coverAt = (wx, wz) => sampleGrid(COVER, wx, wz) / 255;
const world = (p) => { const x = (p.x + 0.5 - GW / 2) * CELL, z = (p.z + 0.5 - GH / 2) * CELL; return [x, heightAt(x, z) + 0.05, z]; };
const PATH_W = PATH.map(world);

// replay state: pos in [0, N-1], interpolated position on the path
function replayAt(t) {
  const s = (t / STEP_SECONDS) % (N + HOLD_STEPS);
  const pos = Math.min(N - 1, s);
  const i = Math.floor(pos), f = pos - i, j = Math.min(N - 1, i + 1);
  const a = PATH_W[i], b = PATH_W[j];
  let traced = 0, ck = 0;
  for (let k = 0; k <= i; k++) { traced += PATH[k].linked; ck += PATH[k].checkpoint ? 1 : 0; }
  return { s, pos, i, f, done: s >= N - 1 + 0.5, cursor: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f], traced, ck };
}

// ---------- 01 · evidence trail: a 3D bar chart, one pillar per evidence commit ----------
// x = evidence step (chronological), z = file lane, height = lines of the issue's code traced to that commit.
// Amber pillars carry an Entire checkpoint (the prompt and session are on record); grey ones do not.
function scene3d(canvas, hudCanvas) {
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false });
  const hud = hudCanvas.getContext("2d");
  const rnd = rng(2077);
  const GAP = 3, LANE = 2.2, VMAX = 5.2, CODEX = [0.5, 0.74, 1];
  const files = [...new Set(PATH.map((p) => p.file))];
  const maxLines = Math.max(...PATH.map((p) => p.lines), 1);
  const X = (i) => (i - (N - 1) / 2) * GAP;
  const Z = (f) => (files.indexOf(f) - (files.length - 1) / 2) * LANE;
  const Hh = (l) => 0.4 + Math.sqrt(l / maxLines) * VMAX;
  const tint = (p) => (!p.checkpoint ? [0.8, 0.76, 0.7] : /codex/i.test(p.agent) ? CODEX : TEAL);
  const bars = PATH.map((p, i) => ({ x: X(i), z: Z(p.file), h: Hh(p.lines), col: tint(p) }));
  const xmin = X(0) - GAP * 0.75, xmax = X(N - 1) + GAP * 0.75, zmin = Z(files[0]) - LANE * 0.8, zmax = Z(files[files.length - 1]) + LANE * 0.8;

  // pillar volume points + cap points + floor haze
  const pos = [], col = [], seed = [], step = [];
  const add = (x, y, z, l, c, st) => { pos.push(x, y, z, l); col.push(...c); seed.push(rnd()); step.push(st); };
  bars.forEach((b, i) => {
    const n = 700 + Math.round(PATH[i].lines * 45);
    for (let k = 0; k < n; k++) add(b.x + (rnd() - 0.5) * 1.1, rnd() * b.h, b.z + (rnd() - 0.5) * 1.1, 0.35 + 0.45 * rnd() + 0.3 * (rnd() > 0.7 ? 1 : 0), b.col, i);
    for (let k = 0; k < 180; k++) add(b.x + (rnd() - 0.5) * 1.1, b.h, b.z + (rnd() - 0.5) * 1.1, 0.9, b.col, i);
  });
  for (let k = 0; k < 5000; k++) add(xmin + rnd() * (xmax - xmin), 0, zmin + rnd() * (zmax - zmin), 0.08 * rnd(), [1, 1, 1], -1);
  const nPts = step.length;
  const pointProg = program(gl, POINT_VS, POINT_FS);
  const pointVao = vao(gl, pointProg, { aPos: { data: new Float32Array(pos), size: 4 }, aCol: { data: new Float32Array(col), size: 3 }, aSeed: { data: new Float32Array(seed), size: 1 }, aStep: { data: new Float32Array(step), size: 1 } });

  // lines: floor grid, lane lines, pillar wireframes
  const lp = [], la = [], lc = [], ls = [];
  const seg = (a, b, alpha, c, st) => { lp.push(...a, ...b); la.push(alpha, alpha); lc.push(...c, ...c); ls.push(st, st); };
  const W3 = [1, 1, 1];
  for (let x = xmin; x <= xmax + 0.01; x += GAP / 2) seg([x, 0, zmin], [x, 0, zmax], Math.round((x - xmin) / (GAP / 2)) % 2 ? 0.05 : 0.1, W3, -1);
  for (let z = zmin; z <= zmax + 0.01; z += LANE) seg([xmin, 0, z], [xmax, 0, z], 0.14, W3, -1);
  seg([xmin, 0, zmin], [xmax, 0, zmin], 0.3, W3, -1); seg([xmin, 0, zmax], [xmax, 0, zmax], 0.3, W3, -1);
  seg([xmin, 0, zmin], [xmin, 0, zmax], 0.3, W3, -1); seg([xmax, 0, zmin], [xmax, 0, zmax], 0.3, W3, -1);
  bars.forEach((b, i) => {
    const x0 = b.x - 0.55, x1 = b.x + 0.55, z0 = b.z - 0.55, z1 = b.z + 0.55, h = b.h;
    const cs = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]];
    for (let k = 0; k < 4; k++) {
      const [ax, az] = cs[k], [bx, bz] = cs[(k + 1) % 4];
      seg([ax, 0, az], [bx, 0, bz], 0.5, b.col, i); seg([ax, h, az], [bx, h, bz], 0.5, b.col, i); seg([ax, 0, az], [ax, h, az], 0.5, b.col, i);
    }
  });
  const lineProg = program(gl, LINE_VS, LINE_FS);
  const lineVao = vao(gl, lineProg, { aPos: { data: new Float32Array(lp), size: 3 }, aA: { data: new Float32Array(la), size: 1 }, aCol: { data: new Float32Array(lc), size: 3 }, aStep: { data: new Float32Array(ls), size: 1 } });
  const nLines = la.length;

  const home = { az: -0.5, el: 0.42, r: 30 };
  const camera = { ...home, idle: -10 };
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, az: camera.az, el: camera.el }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    camera.az = drag.az - (e.clientX - drag.x) * 0.005;
    camera.el = clamp(drag.el + (e.clientY - drag.y) * 0.004, 0.08, 1.3);
    camera.idle = performance.now() / 1000;
  });
  canvas.addEventListener("pointerup", () => (drag = null));
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); camera.r = clamp(camera.r * Math.exp(e.deltaY * 0.001), 16, 70); camera.zoomed = true; camera.idle = performance.now() / 1000; }, { passive: false });
  canvas.addEventListener("dblclick", () => Object.assign(camera, home, { idle: -10, zoomed: false }));

  const glitch = glitcher(canvas, rng(7));
  const fps = { frames: 0, since: 0, value: 60 };
  let baseAz = home.az;

  function frame(t, dt, rp) {
    const { w, h, dpr } = fit(canvas);
    fit(hudCanvas);
    if (!drag && t - camera.idle > 6) {
      if (camera.idle > 0) { baseAz = camera.az - 0.3 * Math.sin(t * 0.12); camera.idle = -10; }
      camera.az = baseAz + 0.3 * Math.sin(t * 0.12);
    }
    if (!camera.zoomed) camera.r = clamp(((xmax - xmin) / 2 + 8) / (Math.tan(19 * Math.PI / 180) * (w / h)), 30, 70);
    const target = [0, 1.7, 0];
    const eye = [target[0] + camera.r * Math.cos(camera.el) * Math.sin(camera.az), target[1] + camera.r * Math.sin(camera.el), target[2] + camera.r * Math.cos(camera.el) * Math.cos(camera.az)];
    const proj = perspective(38 * Math.PI / 180, w / h, 0.5, 260);
    const vp = mul(proj, lookAt(eye, target));
    const ca = bars[rp.i], cb = bars[Math.min(N - 1, rp.i + 1)];
    const cursor = [ca.x + (cb.x - ca.x) * rp.f, ca.h + (cb.h - ca.h) * rp.f, ca.z + (cb.z - ca.z) * rp.f]; // on the pillar tops, where the path runs
    const focus = Math.hypot(...sub(eye, cursor));
    const uStep = rp.done ? N : rp.i;

    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0.012, 0.012, 0.014, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);

    gl.useProgram(lineProg.p);
    gl.uniformMatrix4fv(lineProg.u.uVP, false, vp);
    gl.uniform3fv(lineProg.u.uCam, eye);
    gl.uniform1f(lineProg.u.uStep, uStep);
    gl.bindVertexArray(lineVao);
    gl.drawArrays(gl.LINES, 0, nLines);

    gl.useProgram(pointProg.p);
    gl.uniformMatrix4fv(pointProg.u.uVP, false, vp);
    gl.uniform1f(pointProg.u.uTime, t);
    gl.uniform1f(pointProg.u.uFocus, focus);
    gl.uniform1f(pointProg.u.uAperture, 0.1 * (h / 700));
    gl.uniform1f(pointProg.u.uDpr, dpr);
    gl.uniform1f(pointProg.u.uGain, 0.75);
    gl.uniform1f(pointProg.u.uStep, uStep);
    gl.bindVertexArray(pointVao);
    gl.drawArrays(gl.POINTS, 0, nPts);
    gl.bindVertexArray(null);

    fps.frames++;
    if (t - fps.since > 0.5) { fps.value = fps.frames / (t - fps.since); fps.frames = 0; fps.since = t; }

    // ---- 2D overlay: path, labels, cursor ----
    const c = hud;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const P = (p) => project(vp, p, w, h);
    c.textBaseline = "middle";

    const route = (to, style, width, dash) => {
      c.strokeStyle = style; c.lineWidth = width; c.setLineDash(dash); c.beginPath();
      let open = false;
      for (let k = 0; k <= 96; k++) {
        const u = to * k / 96, i = clamp(Math.floor(u), 0, N - 2), f = u - i;
        const a = bars[i], b = bars[i + 1];
        const s = P([a.x + (b.x - a.x) * f, a.h + (b.h - a.h) * f, a.z + (b.z - a.z) * f]);
        if (!s) { open = false; continue; }
        if (open) c.lineTo(s[0], s[1]); else c.moveTo(s[0], s[1]);
        open = true;
      }
      c.stroke();
    };
    c.lineJoin = "round"; c.lineCap = "round";
    if (N > 1) {                                // a single evidence commit has no path to draw
      route(N - 1, "rgba(255,255,255,0.4)", 1, [2, 3]);
      c.shadowColor = `rgba(${AMBER},0.9)`; c.shadowBlur = 8;
      route(rp.pos, `rgba(${AMBER},0.95)`, 1.6, []);
    }
    c.shadowBlur = 0; c.setLineDash([]); c.lineJoin = "miter"; c.lineCap = "butt";

    bars.forEach((b, i) => {
      const top = P([b.x, b.h + 0.3, b.z]), front = P([b.x, 0, zmax + 0.7]);
      const cur = i === rp.i, past = i <= rp.i || rp.done;
      const colr = b.col.map((v) => Math.round(v * 255)).join(",");
      if (top) {
        c.textAlign = "center";
        c.font = `9px ${FONT}`; c.fillStyle = `rgba(${colr},${cur ? 1 : past ? 0.8 : 0.3})`;
        c.fillText(`E${i + 1}`, top[0], top[1] - 14);
        c.font = `8px ${FONT}`; c.fillStyle = `rgba(255,255,255,${cur ? 0.9 : past ? 0.5 : 0.2})`;
        c.fillText(String(PATH[i].lines), top[0], top[1] - 4);
      }
      if (front) {
        c.textAlign = "center"; c.font = `8px ${FONT}`;
        c.fillStyle = `rgba(255,255,255,${cur ? 0.9 : 0.32})`;
        c.fillText(PATH[i].date.slice(5), front[0], front[1] + 8);
      }
    });
    files.forEach((f) => {
      const s = P([xmin - 0.9, 0, Z(f)]);
      if (!s) return;
      c.textAlign = "right"; c.font = `8px ${FONT}`;
      const hot = f === PATH[rp.i].file && !rp.done;
      c.fillStyle = hot ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.38)";
      const name = f.split("/").pop();
      c.fillText(name.length > 16 ? name.slice(0, 15) + "…" : name, s[0], s[1]);
    });

    const cs = P(cursor);
    if (cs) {
      const pulse = 4 + 3 * ((t * 1.5) % 1);
      c.strokeStyle = `rgba(${AMBER},${0.9 - (pulse - 4) / 4})`; c.lineWidth = 1;
      c.beginPath(); c.arc(cs[0], cs[1], pulse + 4, 0, Math.PI * 2); c.stroke();
      c.fillStyle = `rgb(${AMBER})`;
      c.beginPath(); c.moveTo(cs[0], cs[1] - 5); c.lineTo(cs[0] + 4, cs[1] + 3); c.lineTo(cs[0] - 4, cs[1] + 3); c.fill();
    }
    c.textAlign = "left";
    glitch(c, w, h, dpr, t);
  }

  return { frame, bump() { camera.idle = performance.now() / 1000; } };
}

// occasional horizontal slice shifts and blocks over the viewport
function glitcher(source, rnd) {
  const items = []; let next = 1;
  return (ctx, w, h, dpr, t) => {
    if (t > next) {
      for (let k = 0, n = 1 + Math.floor(rnd() * 4); k < n; k++) {
        const type = rnd() < 0.6 ? "slice" : "bar";
        items.push({ type, x: rnd() * w * 0.85, y: rnd() * h * 0.85, w: type === "bar" ? w * (0.2 + rnd() * 0.5) : 30 + rnd() * 180, h: type === "bar" ? 1 + rnd() * 2 : 6 + rnd() * 40, shift: (rnd() - 0.5) * 60, until: t + 0.06 + rnd() * 0.35 });
      }
      next = t + 0.6 + rnd() * 2.4;
    }
    for (let k = items.length - 1; k >= 0; k--) {
      const g = items[k];
      if (t > g.until) { items.splice(k, 1); continue; }
      if (g.type === "slice") { try { ctx.drawImage(source, g.x * dpr, g.y * dpr, g.w * dpr, g.h * dpr, g.x + g.shift, g.y, g.w, g.h); } catch { /* canvas not ready */ } }
      else if (g.type === "block") { ctx.fillStyle = "#000"; ctx.fillRect(g.x, g.y, g.w, g.h); ctx.fillStyle = "rgba(255,255,255,0.08)"; ctx.fillRect(g.x, g.y + g.h - 2, g.w * 0.6, 1); }
      else { ctx.fillStyle = "rgba(255,255,255,0.5)"; ctx.fillRect(g.x, g.y, g.w, g.h); }
    }
  };
}

// ---------- G · sphere of authoring agents ----------
function globe(canvas, hudCanvas, readout) {
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false });
  const hud = hudCanvas.getContext("2d");
  const rnd = rng(42);
  const ll = (lat, lon, r = 1) => { const a = lat * Math.PI / 180, b = lon * Math.PI / 180; return [r * Math.cos(a) * Math.sin(b), r * Math.sin(a), r * Math.cos(a) * Math.cos(b)]; };
  const HUB = [18, 0];
  const nodes = D.agents.map((a, i) => ({ ...a, lat: 38 - i * 34, lon: 62 + i * 58 }));

  const pts = [], ptT = [];
  const golden = Math.PI * (3 - Math.sqrt(5)), NS = 2600;
  for (let i = 0; i < NS; i++) {
    const y = 1 - 2 * (i + 0.5) / NS, r = Math.sqrt(1 - y * y), th = i * golden;
    pts.push(r * Math.sin(th), y, r * Math.cos(th), 0); ptT.push(-1);
  }
  for (let i = 0; i < 220; i++) { // hub cluster
    const d = Math.sqrt(rnd()) * 5, a = rnd() * Math.PI * 2;
    pts.push(...ll(HUB[0] + Math.sin(a) * d, HUB[1] + Math.cos(a) * d * 1.3, 1.002), 2); ptT.push(-1);
  }
  const nPts = ptT.length;
  const prog = program(gl, GLOBE_VS, GLOBE_FS);
  const ptVao = vao(gl, prog, { aPos: { data: new Float32Array(pts), size: 4 }, aT: { data: new Float32Array(ptT), size: 1 } });

  const ln = [], lnT = [];
  const seg = (a, b, type, ta = -1, tb = -1) => { ln.push(...a, type, ...b, type); lnT.push(ta, tb); };
  for (let lat = -60; lat <= 60; lat += 30) for (let lon = -180; lon < 180; lon += 4) seg(ll(lat, lon, 1.001), ll(lat, lon + 4, 1.001), 1);
  for (let lon = -180; lon < 180; lon += 30) for (let lat = -88; lat < 88; lat += 4) seg(ll(lat, lon, 1.001), ll(lat + 4, lon, 1.001), 1);
  const hubV = ll(...HUB);
  for (const nd of nodes) { // great-circle arcs lifted off the surface
    const to = ll(nd.lat, nd.lon), om = Math.acos(clamp(dot(hubV, to), -1, 1));
    let prev = null;
    for (let k = 0; k <= 64; k++) {
      const u = k / 64, s0 = Math.sin((1 - u) * om) / Math.sin(om), s1 = Math.sin(u * om) / Math.sin(om), lift = 1 + Math.sin(Math.PI * u) * om * 0.22;
      const p = [0, 1, 2].map((q) => (hubV[q] * s0 + to[q] * s1) * lift);
      if (prev) seg(prev.p, p, 1, prev.u, u);
      prev = { p, u };
    }
    seg(ll(nd.lat, nd.lon, 1), ll(nd.lat, nd.lon, 1.12), 2);
  }
  const lnVao = vao(gl, prog, { aPos: { data: new Float32Array(ln), size: 4 }, aT: { data: new Float32Array(lnT), size: 1 } });
  const nLn = lnT.length;
  const eye = [0, 0, 3.3], tilt = 0.32;

  function frame(t) {
    const { w, h, dpr } = fit(canvas);
    fit(hudCanvas);
    const R = Math.min(w, h) * 0.34;
    const fov = 2 * Math.atan(Math.tan(Math.asin(1 / eye[2])) * (h / 2) / R);
    const vp = mul(perspective(fov, w / h, 0.1, 10), lookAt(eye, [0, 0, 0]));
    const spin = -HUB[1] * Math.PI / 180 + t * 0.12 - 0.6;
    const model = rotYX(spin, tilt);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0.012, 0.012, 0.014, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    gl.useProgram(prog.p);
    gl.uniformMatrix4fv(prog.u.uVP, false, vp);
    gl.uniformMatrix4fv(prog.u.uModel, false, model);
    gl.uniform3fv(prog.u.uCam, eye);
    gl.uniform1f(prog.u.uDpr, dpr);
    gl.uniform1f(prog.u.uTime, t);
    gl.uniform1f(prog.u.uLines, 1);
    gl.bindVertexArray(lnVao); gl.drawArrays(gl.LINES, 0, nLn);
    gl.uniform1f(prog.u.uLines, 0);
    gl.bindVertexArray(ptVao); gl.drawArrays(gl.POINTS, 0, nPts);
    gl.bindVertexArray(null);

    // 2D overlay: rim, tick ring, rotating amber arc, node labels
    const c = hud, cx = w / 2, cy = h / 2, mvp = mul(vp, model);
    c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
    c.strokeStyle = "rgba(255,255,255,0.35)"; c.lineWidth = 1;
    c.beginPath(); c.arc(cx, cy, R, 0, Math.PI * 2); c.stroke();
    const rr = R * 1.12;
    c.strokeStyle = "rgba(255,255,255,0.28)"; c.beginPath();
    for (let k = 0; k < 120; k++) {
      const a = k / 120 * Math.PI * 2 - t * 0.05, len = k % 10 === 0 ? 8 : 3;
      c.moveTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr); c.lineTo(cx + Math.cos(a) * (rr + len), cy + Math.sin(a) * (rr + len));
    }
    c.stroke();
    c.strokeStyle = `rgba(${AMBER},0.8)`; c.beginPath(); c.arc(cx, cy, rr + 12, -t * 0.4, -t * 0.4 + 0.5); c.stroke();
    c.font = `8px ${FONT}`; c.textBaseline = "middle";
    nodes.forEach((nd) => {
      const s = project(mvp, ll(nd.lat, nd.lon, 1), w, h);
      if (!s || s[2] > eye[2] - 0.3) return;
      c.strokeStyle = `rgba(${AMBER},0.9)`; c.strokeRect(s[0] - 3, s[1] - 3, 6, 6);
      c.fillStyle = "rgba(255,255,255,0.5)"; c.fillText(`${nd.name.toUpperCase()} ×${nd.count}`, s[0] + 6, s[1]);
    });
    readout.textContent = `${D.totals.checkpointCommits} OF ${D.totals.commits} COMMITS`;
  }
  return { frame };
}

// ---------- 2D panels ----------
// A · ring: outer = replay progress, white = evidence steps walked, amber = checkpoints found
function ringPanel(canvas) {
  const begin = panel2d(canvas);
  return (t, rp) => {
    const { ctx: c, w, h } = begin();
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 4;
    const arc = (r, frac, style, width) => { c.strokeStyle = style; c.lineWidth = width; c.beginPath(); c.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2); c.stroke(); };
    c.strokeStyle = "rgba(255,255,255,0.12)"; c.lineWidth = 1; c.beginPath();
    for (let k = 0; k < N; k++) { const a = -Math.PI / 2 + k / N * Math.PI * 2; c.moveTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); c.lineTo(cx + Math.cos(a) * (R - 4), cy + Math.sin(a) * (R - 4)); }
    c.stroke();
    arc(R, clamp(rp.s / Math.max(1, N - 1), 0, 1), "rgba(255,255,255,0.35)", 1);
    arc(R - 8, (rp.i + 1) / N, "rgba(255,255,255,0.95)", 2);
    arc(R - 13, rp.traced / LINES_TOTAL, "rgba(255,255,255,0.5)", 2);
    arc(R - 18, rp.ck / Math.max(1, D.issue.checkpoints), `rgba(${AMBER},0.9)`, 2);
    c.font = `10px ${FONT}`; label(c, `E${rp.i + 1}`, cx, cy - 4, "center", "rgba(255,255,255,0.95)");
    c.font = `7px ${FONT}`; label(c, PATH[rp.i].date.slice(5), cx, cy + 8, "center", "rgba(255,255,255,0.45)");
  };
}

// C · issues with the most recorded evidence (dotted bar = lines located, thin bar = lines traced)
function issuesList() {
  const rows = D.issues.slice(0, 5), maxL = Math.max(...rows.map((r) => r.lines), 1);
  $("issues").innerHTML = rows.map((r) =>
    `<div class="iss${r.number === D.issue.number ? " hot" : ""}"><div class="iss-top"><span>#${r.number} ${escapeHtml(r.title.toUpperCase())}</span><em>${r.lines} LN · ${r.checkpoints} CK</em></div>` +
    `<div class="iss-bar"><i class="loc" style="width:${r.lines / maxL * 100}%"></i><i class="lnk" style="width:${r.linked / maxL * 100}%"></i></div></div>`).join("");
}

// E · evidence profile: cumulative traced lines over time as a stippled mountain
function profilePanel(canvas) {
  const begin = panel2d(canvas);
  const stip = stippler(8);
  const t0 = Date.parse(PATH[0].date), t1 = Date.parse(PATH[N - 1].date);
  const cum = []; let acc = 0;
  for (const p of PATH) { acc += p.linked; cum.push(acc); }
  $("profile-note").textContent = `${PATH[0].date} → ${PATH[N - 1].date}`;
  const valueAt = (time) => {
    if (N === 1) return cum[0];
    if (time <= Date.parse(PATH[0].date)) return 0;
    let v = 0;
    for (let k = 0; k < N; k++) {
      const tk = Date.parse(PATH[k].date);
      if (time >= tk) v = cum[k]; else { const prevT = Date.parse(PATH[k - 1].date); v = cum[k - 1] + (cum[k] - cum[k - 1]) * (time - prevT) / (tk - prevT || 1); break; }
    }
    return v;
  };
  return (t, rp) => {
    const { ctx: c, w, h } = begin();
    const x0 = 30, x1 = w - 6, top = 10, base = h - 16, maxV = Math.max(acc, 1);
    const X = (time) => x0 + (time - t0) / (t1 - t0 || 1) * (x1 - x0), Y = (v) => base - v / maxV * (base - top);
    for (let k = 0; k <= 2; k++) { const v = maxV * k / 2; c.fillStyle = "rgba(255,255,255,0.07)"; c.fillRect(x0, Y(v), x1 - x0, 1); label(c, String(Math.round(v)), x0 - 4, Y(v), "right", "rgba(255,255,255,0.35)"); }
    for (let x = x0; x <= x1; x += 1.5) {
      const time = t0 + (x - x0) / (x1 - x0) * (t1 - t0), y = Y(valueAt(time)), col = Math.floor(x * 10);
      stip.add(x, y, 1.3, 0.95);
      for (let k = 0, n = 7 + valueAt(time) / maxV * 14; k < n; k++) {
        const fall = -Math.log(1 - hash(col, k) * 0.999) * (base - top) * 0.16;
        if (y + fall < base) stip.add(x + (hash(col, k + 40) - 0.5) * 1.5, y + fall, 1, Math.max(0, 0.7 - fall / ((base - top) * 0.5)));
      }
    }
    stip.flush(c);
    PATH.forEach((p, i) => { const x = X(Date.parse(p.date)); c.fillStyle = `rgba(${p.checkpoint ? AMBER : "255,255,255"},0.3)`; c.fillRect(x, base + 2, 1, 4); });
    const curTime = Date.parse(PATH[rp.i].date) + (Date.parse(PATH[Math.min(N - 1, rp.i + 1)].date) - Date.parse(PATH[rp.i].date)) * rp.f;
    const cx = X(curTime), cyv = Y(valueAt(curTime));
    c.strokeStyle = `rgba(${AMBER},0.6)`; c.beginPath(); c.moveTo(cx, top - 6); c.lineTo(cx, base); c.stroke();
    c.strokeStyle = `rgb(${AMBER})`; c.strokeRect(cx - 3, cyv - 3, 6, 6);
    $("profile-big").textContent = thousands(valueAt(curTime));
  };
}

// H · cumulative commits (solid) and checkpoint-linked commits (amber dashed) since the repo's first commit
function cumulativePanel(canvas) {
  const begin = panel2d(canvas);
  const C = D.cumulative, max = Math.max(C[C.length - 1].commits, 1);
  return (t, rp) => {
    const { ctx: c, w, h } = begin();
    const x0 = 34, x1 = w - 6, top = 10, base = h - 14;
    const X = (i) => x0 + i / (C.length - 1 || 1) * (x1 - x0), Y = (v) => base - v / max * (base - top);
    for (let v = 0; v <= max; v += Math.max(2500, Math.ceil(max / 4 / 2500) * 2500)) { c.fillStyle = "rgba(255,255,255,0.07)"; c.fillRect(x0, Y(v), x1 - x0, 1); label(c, String(v), x0 - 4, Y(v), "right", "rgba(255,255,255,0.35)"); }
    C.forEach((m, i) => label(c, m.m.slice(5), X(i), h - 5, "center", m.m === PATH[rp.i].date.slice(0, 7) ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.35)"));
    const line = (key, style, dash) => {
      c.strokeStyle = style; c.setLineDash(dash); c.lineWidth = 1.2; c.beginPath();
      C.forEach((m, i) => (i ? c.lineTo(X(i), Y(m[key])) : c.moveTo(X(i), Y(m[key]))));
      c.stroke(); c.setLineDash([]);
    };
    line("commits", "rgba(255,255,255,0.9)", []);
    line("checkpoints", `rgba(${AMBER},0.7)`, [3, 3]);
    const mi = C.findIndex((m) => m.m === PATH[rp.i].date.slice(0, 7));
    if (mi >= 0) {
      c.fillStyle = "#fff"; c.fillRect(X(mi) - 2, Y(C[mi].commits) - 2, 4, 4);
      const note = `${C[mi].m} · ${thousands(C[mi].commits)} / ${thousands(C[mi].checkpoints)}`.toUpperCase();
      if ($("cum-note").textContent !== note) $("cum-note").textContent = note;
    }
  };
}

// ---------- tables built from the run records ----------
const money = (v) => (v == null ? "—" : `$${v.toFixed(v < 0.01 ? 4 : 3)}`);
const secs = (v) => (v == null ? "—" : v >= 100 ? `${Math.round(v)}s` : `${v.toFixed(1)}s`);
const ACTION = { fixed: "FIXED", not_fixed: "ASKED", fix_failed: "FAILED", needs_human: "NEEDS HUMAN", error: "ERROR" };

// D · eval: Fence's verdict for closed issues against what happened to them, with the caveat next to the numbers
function evalTable() {
  const E = D.eval, cols = ["mistake", "decision", "no_record"], rows = ["completed", "not_planned", "duplicate"].filter((r) => E.table[r]);
  $("eval-note").textContent = `${E.issues} CLOSED · ${money(E.cost)}`;
  $("eval").innerHTML =
    `<table><thead><tr><th>CLOSED AS</th>${cols.map((c) => `<th class="num">${c.replace("_", " ").toUpperCase()}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r) => `<tr><td>${r.replace("_", " ").toUpperCase()}</td>${cols.map((c) => `<td class="num${(r === "completed" && c === "mistake") || (r === "not_planned" && c === "decision") ? " hit" : ""}">${E.table[r][c] || 0}</td>`).join("")}</tr>`).join("") +
    `</tbody></table>` +
    `<p class="caveat"><b>AGREES ${E.agreement.agree}/${E.agreement.compared}.</b> ALWAYS ANSWERING "MISTAKE" SCORES ${E.baseline.agree}/${E.baseline.compared}. ONE RUN EACH.</p>` +
    `<p class="caveat dim">OPEN SCAN · ${D.scan.issues} ISSUES · ${Object.entries(D.scan.verdicts).map(([k, v]) => `${k.replace("_", " ").toUpperCase()} ${v}`).join(" · ")}</p>`;
}

// F · the same issues through Fence and straight to the worker (baseline)
function compareTable() {
  const by = {};
  for (const r of D.compare.rows) (by[r.issue] ||= {})[r.mode] = r;
  const cell = (r) => (r ? `<td class="act ${r.action}">${ACTION[r.action] || "—"}${r.attempts > 1 ? ` ×${r.attempts}` : ""}</td><td class="num">${money(r.cost)}</td><td class="num t">${secs(r.duration)}</td>` : `<td>—</td><td></td><td class="t"></td>`);
  $("cmp-note").textContent = "ONE RUN PER CELL · SAME VERIFICATION";
  $("compare").innerHTML =
    `<table><thead><tr><th>ISSUE</th><th>VERDICT</th><th>FENCE</th><th class="num">COST</th><th class="num t">TIME</th><th class="sep">BASELINE</th><th class="num">COST</th><th class="num t">TIME</th></tr></thead><tbody>` +
    Object.entries(by).map(([n, m]) => `<tr data-n="${n}" class="${Number(n) === D.issue.number ? "on" : ""}"><td>${D.replays[n] ? `<a href="?issue=${n}">#${n}</a>` : `#${n}`}</td><td class="v ${m.fence.verdict}">${(m.fence.verdict || "—").toUpperCase()}</td>${cell(m.fence)}${cell(m.baseline).replace("<td", '<td class="sep"').replace('class="sep" class="act', 'class="sep act')}</tr>`).join("") +
    `</tbody></table><p class="caveat">ASKED = NO CODE CHANGED, QUESTION WRITTEN FOR A MAINTAINER. FIXED = THE WORKER'S TEST FAILS WITHOUT ITS FIX AND PASSES WITH IT, NOTHING REGRESSED; NO HUMAN REVIEWED THE FIX.</p>`;
}

// I · every issue Fence called "decision", with how that reads against what maintainers did
function decisionsList() {
  const tag = (r) => r.split(",")[0].toUpperCase();
  const col = (title, list) => `<ol><h4>${title}</h4>${list.map((d) => `<li title="${escapeHtml(`${d.quote} — ${d.note}`).replace(/"/g, "&quot;")}" class="${/never|misread/.test(d.reading) ? "" : "done"}"><span>●</span><span>#${d.number} ${escapeHtml(d.title.slice(0, 40))}</span><span>${tag(d.reading)}</span></li>`).join("")}</ol>`;
  const closed = D.decisions.filter((d) => d.group.startsWith("closed")), open = D.decisions.filter((d) => !d.group.startsWith("closed"));
  const real = D.decisions.filter((d) => !/never|misread/.test(d.reading)).length;
  $("dec-note").textContent = `${real} OF ${D.decisions.length} A REAL CHOICE`;
  $("results").innerHTML = col(`CLOSED · ${closed.length}`, closed) + col(`OPEN · ${open.length}`, open);
}

// outcome box: what Fence did with this issue, always on screen
function outcomeBox() {
  const el = $("outcome");
  if (!RUN) { el.innerHTML = `<small>NO FIX RUN RECORDED FOR THIS ISSUE</small>`; return; }
  const head = `<div class="o-head ${RUN.action}"><b>${(RUN.verdict || "—").toUpperCase()}</b><span>→ ${RUN.action === "fixed" ? "FENCE FIXED IT" : RUN.action === "not_fixed" ? "FENCE STOPPED AND ASKED" : (ACTION[RUN.action] || "")}</span><em>CONF ${RUN.confidence?.toFixed(2) ?? "—"}</em></div>`;
  let body;
  if (RUN.action === "fixed") {
    body = `<p>${escapeHtml(RUN.rationale)}</p>` +
      `<dl><dt>WORKER</dt><dd>${RUN.files.length} FILES · ${RUN.attempts} ATTEMPT${RUN.attempts > 1 ? "S" : ""}</dd>` +
      `<dt>VERIFIED BY FENCE</dt><dd>${escapeHtml(RUN.verification.toUpperCase())}</dd>` +
      `<dt>NEW TEST</dt><dd>${escapeHtml(RUN.tests[0] || "—")}</dd>` +
      `<dt>COMMIT</dt><dd>${RUN.commit} · ${escapeHtml(RUN.branch || "")} · WORK COPY ONLY</dd></dl>`;
  } else {
    const q = RUN.quotes[0];
    body = (q ? `<blockquote>${escapeHtml(q.quote)}</blockquote><small class="src">${q.source.replace("_", " ").toUpperCase()} · COMMIT ${q.commit} · CHECKPOINT ${q.checkpoint}</small>` : `<p>${escapeHtml(RUN.rationale)}</p>`) +
      `<dl><dt>FENCE</dt><dd>CHANGED NO CODE · WROTE ${escapeHtml((RUN.question || "A QUESTION").toUpperCase())}</dd>` +
      (BASE ? `<dt>BASELINE</dt><dd class="warn">NOT ASKED · ${ACTION[BASE.action] || ""} · CHANGED ${BASE.files.length} FILES</dd>` : "") + `</dl>`;
  }
  el.innerHTML = head + body + `<div class="o-foot"><span>${money(RUN.cost)} · ${secs(RUN.duration)}</span><span>${BASE ? `BASELINE ${money(BASE.cost)} · ${secs(BASE.duration)}` : ""}</span><span>TRACE ${(RUN.trace || "").slice(0, 8)}</span></div>`;
}

function issueSelector() {
  const sel = $("issue-select");
  sel.innerHTML = Object.values(D.replays).map((r) => `<option value="${r.issue.number}"${r.issue.number === D.issue.number ? " selected" : ""}>#${r.issue.number} · ${(r.fence?.verdict || "—").toUpperCase()} → ${ACTION[r.fence?.action] || "—"}</option>`).join("");
  sel.addEventListener("change", () => { QUERY.set("issue", sel.value); location.search = QUERY.toString(); });
}

// ---------- chrome: ticker, legend, footer, clock, grain, text scramble ----------
const pick = (a) => a[Math.floor(Math.random() * a.length)];
function scramble(el, text, ms = 420) {
  const start = performance.now(), glyphs = "0123456789#%/<>";
  el.dataset.target = text;
  const tick = () => {
    if (el.dataset.target !== text) return;
    const k = (performance.now() - start) / ms;
    if (k >= 1) { el.textContent = text; return; }
    el.textContent = [...text].map((ch, i) => (i / text.length < k || /\s/.test(ch) ? ch : pick(glyphs))).join("");
    requestAnimationFrame(tick);
  };
  tick();
}
function chrome() {
  const spans = [...$("ticker").children];
  const tick = () => { const p = pick(PATH); return `<${p.commit} ${p.file.split("/").pop().toUpperCase()}> ${p.lines} LN`; };
  spans.forEach((s) => (s.textContent = tick()));
  setInterval(() => scramble(pick(spans), tick()), 900);
  $("legend").innerHTML = [[false, "EVIDENCE PATH"], [true, "CHECKPOINT-LINKED"], [false, `ISSUE #${D.issue.number}`], [false, `HEAD ${D.meta.head}`]].map(([amber, text]) => `<span><i class="${amber ? "amber" : ""}"></i>${text}</span>`).join("");
  $("brand-sub").textContent = `${D.meta.repo.toUpperCase()} · NEATHACK 2026`;
  $("footer").innerHTML = `<strong class="copy">FENCE · EVIDENCE BEFORE EDITS</strong>` + PATH.map((p, i) => `<span><i class="${p.checkpoint ? "amber" : ""}"></i>E${i + 1} <b>${p.lines}</b></span>`).join("") + `<em>${D.meta.sources[0]} / ${D.meta.generated.slice(0, 10)}</em>`;
  const grain = $("grain"), gc = grain.getContext("2d"), tile = document.createElement("canvas");
  tile.width = tile.height = 192;
  const tc = tile.getContext("2d"), img = tc.createImageData(192, 192);
  for (let k = 0; k < img.data.length; k += 4) { const v = Math.random() * 255; img.data[k] = img.data[k + 1] = img.data[k + 2] = v; img.data[k + 3] = 255; }
  tc.putImageData(img, 0, 0);
  const pattern = gc.createPattern(tile, "repeat");
  const clock = () => { const d = new Date(); $("clock").textContent = d.toISOString().slice(11, 19); $("date").textContent = `${d.toISOString().slice(0, 10)} · UTC`; };
  clock();
  return (n) => {
    if (n % 2 === 0) {
      if (grain.width !== innerWidth || grain.height !== innerHeight) { grain.width = innerWidth; grain.height = innerHeight; }
      gc.setTransform(1, 0, 0, 1, Math.random() * 192, Math.random() * 192);
      gc.fillStyle = pattern; gc.fillRect(-192, -192, grain.width + 192, grain.height + 192);
    }
    if (n % 15 === 0) clock();
  };
}

// ---------- static DOM: evidence table, stage strip ----------
$("replay-note").textContent = `${N} STEPS · ${STEP_SECONDS} S`;
$("chain-note").textContent = `${D.issue.checkpoints} OF ${N} WITH CHECKPOINT`;
$("globe-note").textContent = `${D.agents.length} AGENTS`;
$("view-title").textContent = `EVIDENCE TRAIL · ISSUE #${D.issue.number}`;
$("view-note").textContent = D.issue.title.toUpperCase().slice(0, 64);
$("checkpoints").innerHTML = PATH.map((p, i) => `<tr><td>E${i + 1}</td><td class="name">${p.subject}</td><td class="num">${p.date.slice(5)}</td><td class="num">${p.lines}</td><td class="num ck">${p.checkpoint ? "●" : "–"}</td></tr>`).join("");
const tableRows = [...$("checkpoints").rows];
const STAGES = [["ISSUE", `#${D.issue.number}`, "st-issue"], ["LOCATE", `${D.spans.length} SPANS`, "st-locate"], ["BLAME", `${LINES_TOTAL} LN`, "st-blame"], ["CHECKPOINTS", `0/${N}`, "st-ck"], ["INTENT", "0 LN", "st-intent"], ["VERDICT", "—", "st-verdict"], ["ACTION", "—", "st-action"]];
const VERDICT_TEXT = RUN?.verdict ? RUN.verdict.toUpperCase() : "NOT RUN", ACTION_TEXT = RUN ? (RUN.action === "not_fixed" ? "ASKED A HUMAN" : ACTION[RUN.action] || "—") : "—";
$("stages").innerHTML = STAGES.map(([k, v, id]) => `<div class="stage" id="${id}"><small>${k}</small><b>${v}</b></div>`).join("") + `<i class="bar" id="st-bar"></i>`;
const escapeHtml = (t) => t.replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));

// ---------- replay-driven DOM: KPIs, table highlight, stage strip, step card, run log ----------
const logEl = $("log");
function logLine(text, warn = false) {
  const li = document.createElement("li");
  li.innerHTML = `<time>${new Date().toISOString().slice(11, 19)}</time><span></span>`;
  if (warn) li.classList.add("warn");
  logEl.prepend(li);
  while (logEl.children.length > 12) logEl.lastElementChild.remove();
  const span = li.querySelector("span");
  let k = 0;
  const type = () => { span.textContent = text.slice(0, ++k) + (k < text.length ? "▌" : ""); if (k < text.length) setTimeout(type, 12); };
  type();
}
function setCard(p, i) {
  scramble($("c-id"), `E${i + 1}`);
  scramble($("c-commit"), p.commit);
  $("c-date").textContent = p.date;
  $("c-subj").textContent = p.subject;
  $("c-file").textContent = p.file;
  $("c-lines").textContent = `${p.lines} LN TRACED`;
  const ck = $("c-ck");
  ck.textContent = p.checkpoint ? `CHECKPOINT ${p.checkpoint} · ${p.agent.toUpperCase()}` : "NO CHECKPOINT ON RECORD · AUTHOR UNKNOWN TO ENTIRE";
  ck.classList.toggle("none", !p.checkpoint);
  $("c-prompt").textContent = p.prompt || "No prompt recorded for this commit.";
}
let lastStep = -1, lastDone = false, lastKpi = 0;
function syncDom(t, rp) {
  if (rp.i < lastStep) { logEl.innerHTML = ""; lastStep = -1; lastDone = false; logLine(`ISSUE #${D.issue.number} OPENED · ${D.issue.created}`); }
  if (lastStep < rp.i) {
    while (lastStep < rp.i) {
      const p = PATH[++lastStep];
      logLine(`E${lastStep + 1} ${p.commit} · ${p.file.split("/").pop()} · ${p.lines} LN${p.checkpoint ? ` · CK ${p.checkpoint.slice(0, 8)} ${p.agent.toUpperCase()}` : " · NO CHECKPOINT"}`, !!p.checkpoint);
    }
    setCard(PATH[rp.i], rp.i);
  }
  if (rp.done && !lastDone) {
    lastDone = true;
    logLine(`END OF RECORD · ${rp.traced}/${LINES_TOTAL} LN TRACED`);
    if (RUN) {
      logLine(`VERDICT ${VERDICT_TEXT} · CONF ${RUN.confidence?.toFixed(2)} · ${money(RUN.cost)}`, true);
      logLine(RUN.action === "fixed" ? `WORKER FIXED · FENCE VERIFIED · ${RUN.tests.length} TEST(S) NEWLY PASSING · COMMIT ${RUN.commit}` : `NO CODE CHANGED · QUESTION WRITTEN · ${RUN.question || ""}`, true);
    }
  }
  if (t - lastKpi > 0.6) {
    lastKpi = t;
    const p = PATH[rp.i], next = PATH[rp.i + 1];
    scramble($("kpi-step"), `${rp.i + 1}/${N}`);
    scramble($("kpi-commit"), p.commit.slice(0, 7));
    scramble($("kpi-lines"), thousands(rp.traced));
    $("kpi-next").textContent = next ? next.commit.slice(0, 7) : "END";
    tableRows.forEach((r, i) => { r.classList.toggle("on", i === rp.i); r.classList.toggle("pending", i > rp.i); });
    const on = tableRows[rp.i];
    if (on) on.closest(".body").scrollTop = on.offsetTop - 40;
    $("st-ck").querySelector("b").textContent = `${rp.ck}/${N}`;
    $("st-intent").querySelector("b").textContent = `${thousands(rp.traced)} LN`;
    $("st-ck").classList.toggle("live", !rp.done);
    $("st-intent").classList.toggle("live", !rp.done);
    $("st-verdict").querySelector("b").textContent = rp.done ? VERDICT_TEXT : "—";
    $("st-verdict").classList.toggle("on", rp.done);
    $("st-action").querySelector("b").textContent = rp.done ? ACTION_TEXT : "—";
    $("st-action").classList.toggle("on", rp.done);
    $("st-bar").style.width = `${clamp(rp.s / Math.max(1, N - 1), 0, 1) * 100}%`;
  }
}

// ---------- boot ----------
const scene = scene3d($("scene"), $("scene-hud"));
const sphere = globe($("globe"), $("globe-hud"), $("globe-readout"));
const panels = [ringPanel($("ring")), profilePanel($("profile")), cumulativePanel($("cumulative"))];
evalTable(); compareTable(); decisionsList(); outcomeBox(); issueSelector(); issuesList();
const tickChrome = chrome();
logLine(`ISSUE #${D.issue.number} OPENED · ${D.issue.created}`);

const T_OFFSET = Number(new URLSearchParams(location.search).get("t") || 0); // ?t=20 starts the replay 20 s in
let last = performance.now(), n = 0;
function loop(now) {
  const t = now / 1000 + T_OFFSET, dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  const rp = replayAt(t);
  scene.frame(t, dt, rp);
  sphere.frame(t);
  for (const p of panels) p(t, rp);
  syncDom(t, rp);
  tickChrome(n++);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
