// The part in 3D: the stock after an operation, turned around the axis. The profiles come from the simulation
// (gcode/stock3d.py), nothing is drawn that is not in them. three.js is a file of the project (static/vendor/three):
// no internet needed. It is loaded only when a page has a .part3d element.
//
// <div class="part3d" data-src="…/part3d.json" data-mode="program|order"> … </div>
//   program: a slider 0…N, the state after operation k: done before / removed by operation k / not machined yet;
//            the material operation k removes is shown as a ghost.
//   order:   the state after the blocks the operator marked done; the next operation's material highlighted; the
//            finished part (the drawing's profile) as a transparent body and an outline.

const SEGMENTS = 48;  // around the axis
const CLASSES = ["current", "done", "bar"];  // a face between two classes takes the first

function radiusAt(points, z) {  // points from the end face (largest z) to the jaws
  for (let i = 1; i < points.length; i++) {
    const [z0, r0] = points[i - 1], [z1, r1] = points[i];
    if (z <= z0 && z >= z1) return z0 === z1 ? Math.min(r0, r1) : r0 + (r1 - r0) * (z - z0) / (z1 - z0);
  }
  return 0;
}

// the class of a profile segment: compared with the state before ("current": that operation removed it) and the bar
function classify(p0, p1, before, bar, near) {
  const probe = z => {
    const r = radiusAt(near, z), b = radiusAt(before, z);
    if (before !== near && r < b - 0.005) return "current";
    return r < bar - 0.005 ? "done" : "bar";
  };
  if (Math.abs(p0[0] - p1[0]) > 1e-6) return probe((p0[0] + p1[0]) / 2);
  const a = probe(p0[0] + 0.02), b = probe(p0[0] - 0.02);  // a face: the stronger of its two sides
  return CLASSES.indexOf(a) <= CLASSES.indexOf(b) ? a : b;
}

function closed(points) {  // down to the axis at both ends
  const out = points.slice();
  if (out[0][1] > 0) out.unshift([out[0][0], 0]);
  if (out[out.length - 1][1] > 0) out.push([out[out.length - 1][0], 0]);
  return out;
}

// a body of revolution, one group per class: part z along world x, the radius around it
function lathe(THREE, points, classOf) {
  const buf = {};
  const pts = closed(points);
  for (let i = 1; i < pts.length; i++) {
    const [z0, r0] = pts[i - 1], [z1, r1] = pts[i];
    if (r0 < 1e-6 && r1 < 1e-6) continue;
    const cls = classOf(pts[i - 1], pts[i]);
    if (!cls) continue;
    const b = buf[cls] || (buf[cls] = {pos: [], nor: []});
    let nz = r1 - r0, nr = -(z1 - z0);  // outward normal of the segment (z goes down towards the jaws)
    const len = Math.hypot(nz, nr) || 1;
    nz /= len; nr /= len;
    for (let s = 0; s < SEGMENTS; s++) {
      const a0 = s / SEGMENTS * Math.PI * 2, a1 = (s + 1) / SEGMENTS * Math.PI * 2;
      const c0 = Math.cos(a0), s0 = Math.sin(a0), c1 = Math.cos(a1), s1 = Math.sin(a1);
      const v = (z, r, c, sn) => [z, r * c, r * sn];
      const n = (c, sn) => [nz, nr * c, nr * sn];
      const quad = [v(z0, r0, c0, s0), v(z1, r1, c0, s0), v(z1, r1, c1, s1), v(z0, r0, c0, s0), v(z1, r1, c1, s1),
                    v(z0, r0, c1, s1)];
      const norms = [n(c0, s0), n(c0, s0), n(c1, s1), n(c0, s0), n(c1, s1), n(c1, s1)];
      for (const p of quad) b.pos.push(...p);
      for (const p of norms) b.nor.push(...p);
    }
  }
  const out = {};
  for (const cls in buf) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(buf[cls].pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(buf[cls].nor, 3));
    out[cls] = g;
  }
  return out;
}

function colours(el) {
  const css = getComputedStyle(el), get = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
  return {done: get("--p3d-done", "#9db0c4"), current: get("--p3d-current", "#e8892b"), bar: get("--p3d-bar", "#7a6a58"),
          next: get("--p3d-next", "#e8892b"), final: get("--p3d-final", "#2f5fd0"), bg: get("--p3d-bg", "#f3f5f8")};
}

let three = null;
async function load() {
  if (!three) three = Promise.all([import("three"), import("/static/vendor/three/OrbitControls.js")]);
  return three;
}

// One WebGL renderer for the page (a browser allows only a few contexts): every view renders with it and copies the
// picture into its own 2D canvas.
let shared = null;
function sharedRenderer(THREE) {
  if (!shared) shared = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  return shared;
}
const views = new Set();

// the camera at the part's size: the whole part (from the end face to the jaws, the bar's Ø) in the view
function fitCamera(camera, controls, span, radius) {
  const r = Math.hypot(span / 2, radius) * 1.08;
  const vfov = camera.fov * Math.PI / 180, hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
  const dist = r / Math.sin(Math.min(vfov, hfov) / 2);
  const dir = {x: 0.18, y: 0.45, z: 0.87};  // a little from above and in front
  camera.position.set(dir.x * dist, dir.y * dist, dir.z * dist);
  camera.near = dist / 100; camera.far = dist * 10;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
}

async function mount(el) {
  if (el.dataset.mounted) return;
  el.dataset.mounted = "1";
  const view = el.querySelector(".part3d-view"), info = el.querySelector(".part3d-info");
  let THREE, OrbitControls;
  try {
    const [mod, ctl] = await load();
    THREE = mod; OrbitControls = ctl.OrbitControls;
  } catch (e) {
    info.textContent = t("3D view not loaded: {error}", {error: e.message});
    return;
  }
  const data = await fetch(el.dataset.src).then(r => r.json());
  if (data.error) { info.textContent = data.error; return; }
  const notes = [];
  if (data.note) notes.push(data.note);
  if (data.computed_at) notes.push(t("Program {n} was made before the states were kept: simulated again from its text {at} with the job's and the turret's data of then.", {n: data.program, at: data.computed_at}));
  if (data.stale && data.stale.length) notes.push(t("Program {n} is out of date: {reasons}.", {n: data.program, reasons: data.stale.join("; ")}));
  info.textContent = notes.join(" ");
  let renderer;
  try {
    renderer = sharedRenderer(THREE);
  } catch (e) {
    view.textContent = t("This browser cannot show 3D (no WebGL): use the 2D simulation on the program's page.");
    return;
  }
  const col = colours(el);
  const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d");
  view.appendChild(canvas);
  const fit = document.createElement("button");
  fit.type = "button"; fit.className = "small part3d-fit"; fit.textContent = t("Fit");
  view.after(fit);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 10000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(1, 2, 3);
  camera.add(sun);
  scene.add(camera);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  const group = new THREE.Group();
  scene.add(group);

  const states = data.states, bar = data.bar_radius;
  const zTop = states[0].points[0][0], zEnd = data.z_jaws;
  const centre = (zTop + zEnd) / 2, span = Math.max(zTop - zEnd, 1);
  group.position.x = -centre;

  const mats = {};
  const material = (key, colour, opacity) => mats[key] || (mats[key] = new THREE.MeshLambertMaterial({
    color: colour, side: THREE.DoubleSide, transparent: opacity < 1, opacity, depthWrite: opacity >= 1}));

  function clear() {
    for (const child of group.children.slice()) {
      group.remove(child);
      if (child.geometry) child.geometry.dispose();
    }
  }
  function add(geoms, materialOf) {
    for (const cls in geoms) group.add(new THREE.Mesh(geoms[cls], materialOf(cls)));
  }
  function outline(points, colour) {  // the drawing's profile in the plane facing the viewer, both halves
    const pts = closed(points);
    for (const sign of [1, -1]) {
      const g = new THREE.BufferGeometry().setFromPoints(pts.map(([z, r]) => new THREE.Vector3(z, sign * r, 0)));
      const line = new THREE.Line(g, new THREE.LineBasicMaterial({color: colour, depthTest: false}));
      line.renderOrder = 10;
      group.add(line);
    }
  }

  let w = 300, h = 220;
  function render() {
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(ratio);
    renderer.setClearColor(col.bg);
    renderer.setSize(w, h, false);
    renderer.render(scene, camera);
    if (canvas.width !== renderer.domElement.width || canvas.height !== renderer.domElement.height) {
      canvas.width = renderer.domElement.width; canvas.height = renderer.domElement.height;
    }
    ctx.drawImage(renderer.domElement, 0, 0);
  }
  let fitted = false;
  function resize() {
    w = view.clientWidth || 300;
    h = Math.max(200, Math.min(420, Math.round(w * 0.5)));
    canvas.style.width = "100%";
    canvas.style.height = h + "px";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    if (!fitted && view.clientWidth) { fitCamera(camera, controls, span, bar); fitted = true; }
    render();
  }
  controls.addEventListener("change", render);
  fit.addEventListener("click", () => { fitCamera(camera, controls, span, bar); render(); });
  const observer = new ResizeObserver(resize);
  observer.observe(view);
  const entry = {el, theme: () => { Object.assign(col, colours(el)); render(); }, dispose: () => {
    observer.disconnect(); controls.dispose(); clear();
    for (const key in mats) mats[key].dispose();
  }};
  views.add(entry);

  if (el.dataset.mode === "order") {
    const k = data.done, shown = states[k].points, next = states[k + 1];
    clear();
    const opaque = k === states.length - 1 ? 1 : 0.8;
    add(lathe(THREE, shown, (p0, p1) => {
      if (next && classify(p0, p1, shown, bar, next.points) === "current") return "next";
      return classify(p0, p1, shown, bar, shown);
    }), cls => material("o-" + cls, col[cls === "next" ? "next" : cls], opaque));
    if (data.final.length) {
      add(lathe(THREE, data.final, () => "final"), () => material("final", col.final, 0.25));
      outline(data.final, col.final);
    }
  } else {
    const slider = el.querySelector(".part3d-step"), where = el.querySelector(".part3d-where");
    slider.max = states.length - 1;
    slider.value = states.length - 1;
    const show = () => {
      const k = +slider.value, now = states[k].points, before = states[Math.max(0, k - 1)].points;
      clear();
      add(lathe(THREE, now, (p0, p1) => classify(p0, p1, before, bar, now)), cls => material(cls, col[cls], 1));
      if (k > 0) {  // the material this operation removed: a ghost
        add(lathe(THREE, before, (p0, p1) => {
          const z = Math.abs(p0[0] - p1[0]) > 1e-6 ? (p0[0] + p1[0]) / 2 : p0[0];
          const lost = Math.max(radiusAt(before, z + 0.02), radiusAt(before, z - 0.02), radiusAt(before, z))
            > Math.min(radiusAt(now, z + 0.02), radiusAt(now, z - 0.02), radiusAt(now, z)) + 0.005;
          return lost ? "ghost" : null;
        }), () => material("ghost", col.current, 0.18));
      }
      where.textContent = k === 0 ? t("operation 0 of {total}: the bar", {total: states.length - 1})
        : t("operation {k} of {total}: N{n} {title}", {k: k, total: states.length - 1, n: states[k].n, title: states[k].title});
      render();
    };
    slider.addEventListener("input", show);
    show();
  }
  resize();
}

// views whose element left the page (the shop card shows another machine): their scenes are freed
function prune() {
  for (const v of views) if (!v.el.isConnected) { v.dispose(); views.delete(v); }
}

function mountAll(root) {
  prune();
  (root || document).querySelectorAll(".part3d[data-src]").forEach(el => {
    const go = () => mount(el).catch(e => {
      const info = el.querySelector(".part3d-info");
      if (info) info.textContent = t("3D view: {error}", {error: e.message});
    });
    if (el.tagName === "DETAILS" && !el.open) el.addEventListener("toggle", () => { if (el.open) go(); });
    else go();
  });
}

addEventListener("tp-theme", () => { for (const v of views) v.theme(); });  // the top bar's theme button
window.turnpilotPart3d = {mountAll, prune};
mountAll(document);
