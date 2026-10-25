// The shop in 3D (stage 7.2b): the scene turnpilot/shop_scene.py builds from the plan and the machines (in mm), drawn
// with three.js; looking at it only: every change of a place is made in 2D. The page's one WebGL renderer
// (static/webgl.js) draws it, and the picture is copied into this view's canvas; it is drawn only when it changes,
// with simple materials and no shadows. Click a machine: the same card as in 2D (window.turnpilotShop.select).
//
// <div id="sf-3d" data-scene="#sf-scene"> with <script type="application/json" id="sf-scene">…</script>
// Coordinates: the plan's X → x, its Y → z, the height → y; 1 unit = 1 m.

import { loadThree, sharedRenderer } from "./webgl.js";

const MM = 0.001;
const ROLE = {body: "--sf-mach", dark: "--sf-mach2", glass: "--sf-glass", stone: "--sf-stone", accent: "--sf-ink2"};
const STATUS = {running: "--sf-run", setup: "--sf-setup", stopped: "--sf-stop", free: "--sf-free"};
const OPENING = {door: "--sf-accent", roller_door: "--sf-accent", exit: "--sf-run", window: "--sf-glass"};

function css(el, name, fallback) {
  return (getComputedStyle(el).getPropertyValue(name) || fallback).trim() || fallback;
}

// a text on a sprite (a label of the plan, a machine's name, a "!" or a "?"); height in m
function textSprite(THREE, text, height, colour, background) {
  const pad = 8, font = 48;
  const c = document.createElement("canvas"), g = c.getContext("2d");
  g.font = `600 ${font}px system-ui, sans-serif`;
  c.width = Math.ceil(g.measureText(text).width) + 2 * pad; c.height = font + 2 * pad;
  g.font = `600 ${font}px system-ui, sans-serif`;
  if (background) { g.fillStyle = background; g.fillRect(0, 0, c.width, c.height); }
  g.fillStyle = colour; g.textBaseline = "middle"; g.fillText(text, pad, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, depthTest: false, transparent: true}));
  s.scale.set(height * c.width / c.height, height, 1);
  s.renderOrder = 5;
  return s;
}

function part(THREE, p, material) {
  let geom;
  if (p.shape === "box") {
    geom = new THREE.BoxGeometry(p.sx * MM, p.sh * MM, p.sy * MM);
  } else {
    geom = new THREE.CylinderGeometry(p.r * MM, p.r * MM, p.length * MM, 24);
    if (p.axis === "x") geom.rotateZ(Math.PI / 2);
    if (p.axis === "y") geom.rotateX(Math.PI / 2);
  }
  const mesh = new THREE.Mesh(geom, material);
  mesh.position.set(p.x * MM, p.h * MM, p.y * MM);
  return mesh;
}

function outline(THREE, w, h, d, colour, dashed) {
  const geom = new THREE.EdgesGeometry(new THREE.BoxGeometry(w, h, d));
  const mat = dashed ? new THREE.LineDashedMaterial({color: colour, dashSize: .3, gapSize: .2})
    : new THREE.LineBasicMaterial({color: colour});
  const lines = new THREE.LineSegments(geom, mat);
  if (dashed) lines.computeLineDistances();
  return lines;
}

async function start(el) {
  const scene3 = JSON.parse(document.querySelector(el.dataset.scene).textContent);
  const [THREE, {OrbitControls}] = await loadThree();
  let renderer;
  try { renderer = sharedRenderer(THREE); } catch (e) {
    el.querySelector(".sf-3d-view").textContent = t("This browser cannot show 3D (no WebGL): use the 2D plan.");
    return null;
  }
  const view = el.querySelector(".sf-3d-view");
  const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d");
  canvas.style.width = "100%"; canvas.style.height = "100%"; canvas.style.display = "block";
  canvas.style.touchAction = "none";
  view.appendChild(canvas);
  const colour = name => css(el, name, "#8A979F");

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x556677, 1.8));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.position.set(-1, 3, 2);
  scene.add(sun);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.zoomToCursor = true;  // the wheel and two fingers zoom to the point under them, not to the centre
  controls.maxPolarAngle = Math.PI / 2 - 0.05;  // stays above the floor
  controls.screenSpacePanning = false;

  const W = scene3.floor.w * MM, D = scene3.floor.d * MM;
  const mats = {};
  const mat = (key, c, opacity = 1) => mats[key] || (mats[key] = new THREE.MeshLambertMaterial(
    {color: c, transparent: opacity < 1, opacity, depthWrite: opacity >= 1}));

  // the floor
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D), mat("floor", colour("--sf-sheet")));
  floor.rotation.x = -Math.PI / 2; floor.position.set(W / 2, 0, D / 2);
  scene.add(floor);
  // the walls: a box per segment
  // walls and columns: one grey; see-through when the camera looks down on the shop (they do not hide the machines)
  const wallMaterial = new THREE.MeshLambertMaterial({color: colour("--sf-ink2"), transparent: true, opacity: 0.85});
  scene3.walls.forEach(w => {
    const dx = (w.x1 - w.x0) * MM, dz = (w.y1 - w.y0) * MM, len = Math.hypot(dx, dz) + w.thickness * MM;
    const m = new THREE.Mesh(new THREE.BoxGeometry(len, w.height * MM, w.thickness * MM), wallMaterial);
    m.position.set((w.x0 + w.x1) / 2 * MM, w.height / 2 * MM, (w.y0 + w.y1) / 2 * MM);
    m.rotation.y = -Math.atan2(dz, dx);
    scene.add(m);
  });
  // doors, roller doors, exits and windows: panels on their walls (the walls are not cut)
  scene3.openings.forEach(o => {
    const along = o.along === "x", t = 0.45;
    const m = new THREE.Mesh(new THREE.BoxGeometry(along ? o.width * MM : t, o.height * MM, along ? t : o.width * MM),
      mat("op-" + o.kind, colour(OPENING[o.kind] || "--sf-accent"), 0.9));
    m.position.set((o.x + (along ? o.width / 2 : 0)) * MM, (o.sill + o.height / 2) * MM,
      (o.y + (along ? 0 : o.width / 2)) * MM);
    scene.add(m);
  });
  // the columns
  scene3.columns.forEach(c => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(c.size * MM, c.height * MM, c.size * MM), wallMaterial);
    m.position.set(c.x * MM, c.height / 2 * MM, c.y * MM);
    scene.add(m);
  });
  // the floor areas: walkways marked (yellow edges), zones dashed, storage and fixtures filled; their labels
  const text = Math.max(W, D) / 90;
  scene3.areas.forEach(a => {
    const w = a.w * MM, d = a.d * MM, cx = (a.x + a.w / 2) * MM, cz = (a.y + a.d / 2) * MM;
    if (a.role === "storage" || a.role === "fixture") {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), mat("area-" + a.role,
        colour(a.role === "storage" ? "--sf-mach2" : "--sf-mach"), 0.8));
      m.rotation.x = -Math.PI / 2; m.position.set(cx, 0.004, cz);
      scene.add(m);
    }
    const edge = outline(THREE, w, 0.001, d, colour(a.role === "walkway" ? "--sf-aisle" : "--sf-ink2"), a.role === "zone");
    edge.position.set(cx, 0.006, cz);
    scene.add(edge);
    if (a.label) {
      const s = textSprite(THREE, a.label, text * 0.6, colour("--sf-ink2"));
      s.position.set(cx, 0.3, cz);
      scene.add(s);
    }
  });
  scene3.labels.forEach(lb => {
    const s = textSprite(THREE, lb.text, text * 0.8, colour("--sf-ink"));
    s.position.set(lb.x * MM, 0.4, lb.y * MM);
    scene.add(s);
  });

  // the machines
  const machines = new Map(), pickable = [];
  scene3.machines.forEach(m => {
    const g = new THREE.Group();
    g.position.set(m.x * MM, 0, m.y * MM);
    g.rotation.y = -m.rotation * Math.PI / 180;  // as the plan's rotate(), with Y down
    const own = {};  // its own materials (a dimmed machine fades alone)
    const material = role => own[role] || (own[role] = role === "status"
      ? new THREE.MeshBasicMaterial({color: colour(STATUS[m.status] || "--sf-free")})  // unlit: bright from afar
      : new THREE.MeshLambertMaterial({color: colour(ROLE[role] || "--sf-mach"), transparent: role === "glass",
                                       opacity: role === "glass" ? 0.45 : 1}));
    m.parts.forEach(p => { const mesh = part(THREE, p, material(p.role)); mesh.userData.id = m.id; g.add(mesh); pickable.push(mesh); });
    if (!m.known) {  // no type or footprint: a grey frame and "?", as in 2D
      const frame = outline(THREE, m.length * MM, m.height * MM, m.width * MM, colour("--sf-free"));
      frame.position.y = m.height / 2 * MM;
      g.add(frame);
      const hit = new THREE.Mesh(new THREE.BoxGeometry(m.length * MM, m.height * MM, m.width * MM),
        new THREE.MeshBasicMaterial({visible: false}));
      hit.position.y = m.height / 2 * MM; hit.userData.id = m.id; g.add(hit); pickable.push(hit);
      const q = textSprite(THREE, "?", 0.6, colour("--sf-ink2"));
      q.position.y = m.height / 2 * MM; g.add(q);
    }
    if (m.conflicts.length) {
      const clash = outline(THREE, m.length * MM + 0.4, m.height * MM + 0.2, m.width * MM + 0.4, colour("--sf-warn"), true);
      clash.position.y = (m.height * MM + 0.2) / 2; g.add(clash);
    }
    const sel = outline(THREE, m.length * MM + 0.25, m.height * MM + 0.1, m.width * MM + 0.25, colour("--sf-accent"));
    sel.position.y = (m.height * MM + 0.1) / 2; sel.visible = false; g.add(sel);
    scene.add(g);
    const label = textSprite(THREE, m.name.toUpperCase() + (m.height_source === "conditional" ? " *" : ""), 0.35,
      colour("--sf-ink"), colour("--sf-sheet"));
    label.position.set(m.x * MM, m.height * MM + 0.45, m.y * MM);
    scene.add(label);
    let alarm = null;
    if (m.alarm) {
      alarm = textSprite(THREE, "!", 0.7, colour("--sf-stop"));
      alarm.position.set(m.x * MM, m.height * MM + 1.1, m.y * MM);
      scene.add(alarm);
    }
    machines.set(m.id, {group: g, materials: own, sel, label, alarm,
                        centre: new THREE.Vector3(m.x * MM, m.height / 2 * MM, m.y * MM),
                        size: Math.max(m.length, m.width, m.height) * MM});
  });

  const up = new THREE.Vector3();
  let w = 600, h = 400;
  function render() {
    // from above (more than 25° over the floor) the walls are see-through; from the side, solid
    up.subVectors(camera.position, controls.target);
    const above = Math.asin(Math.min(1, up.y / (up.length() || 1))) > 25 * Math.PI / 180;
    wallMaterial.opacity = above ? 0.28 : 0.85;
    wallMaterial.depthWrite = !above;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(colour("--sf-paper"));
    renderer.setSize(w, h, false);
    renderer.render(scene, camera);
    if (canvas.width !== renderer.domElement.width || canvas.height !== renderer.domElement.height) {
      canvas.width = renderer.domElement.width; canvas.height = renderer.domElement.height;
    }
    ctx.drawImage(renderer.domElement, 0, 0);
  }
  // the whole plan in view, from above at an angle (from the plan's bottom edge)
  function fit() {
    const r = Math.hypot(W, D) / 2;
    const vfov = camera.fov * Math.PI / 180, hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = r / Math.sin(Math.min(vfov, hfov) / 2) * 0.9;
    const tilt = 55 * Math.PI / 180;
    camera.position.set(W / 2, dist * Math.sin(tilt), D / 2 + dist * Math.cos(tilt));
    camera.near = dist / 200; camera.far = dist * 6;
    camera.updateProjectionMatrix();
    controls.target.set(W / 2, 0, D / 2);
    controls.update();
  }
  let fitted = false;
  function resize() {
    w = view.clientWidth || 600; h = view.clientHeight || 400;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (!fitted && view.clientWidth) { fit(); fitted = true; }
    render();
  }
  controls.addEventListener("change", render);
  const observer = new ResizeObserver(resize);
  observer.observe(view);
  el.querySelector(".sf-3d-fit").addEventListener("click", () => { fit(); render(); });

  // a click (not a drag) on a machine: its card, as in 2D
  const ray = new THREE.Raycaster(), down = {x: 0, y: 0};
  canvas.addEventListener("pointerdown", e => { down.x = e.clientX; down.y = e.clientY; });
  canvas.addEventListener("pointerup", e => {
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
    const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1),
      camera);
    const hit = ray.intersectObjects(pickable, false)[0];
    if (hit && window.turnpilotShop) window.turnpilotShop.select(hit.object.userData.id);
  });

  // a double click on a machine: the camera flies to it (and its card opens, as on a click)
  let flight = null;
  function flyTo(v) {
    const from = {p: camera.position.clone(), t: controls.target.clone()};
    const dir = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
    const to = {t: v.centre.clone(), p: v.centre.clone().addScaledVector(dir, Math.max(4, v.size * 2.5))};
    const t0 = performance.now(), ms = 600;
    if (flight) cancelAnimationFrame(flight);
    const step = now => {
      const k = Math.min(1, (now - t0) / ms), e = k * k * (3 - 2 * k);
      camera.position.lerpVectors(from.p, to.p, e);
      controls.target.lerpVectors(from.t, to.t, e);
      controls.update();
      render();
      flight = k < 1 ? requestAnimationFrame(step) : null;
    };
    flight = requestAnimationFrame(step);
  }
  canvas.addEventListener("dblclick", e => {
    const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1),
      camera);
    const hit = ray.intersectObjects(pickable, false)[0];
    if (!hit) return;
    const id = hit.object.userData.id;
    if (window.turnpilotShop) window.turnpilotShop.select(id);
    flyTo(machines.get(id));
  });

  return {
    render, resize,
    dispose() { observer.disconnect(); controls.dispose(); },
    select(id) { machines.forEach((v, key) => { v.sel.visible = key === id; }); render(); },
    dim(shown) {  // the search and the status filter of the page: the others fade
      machines.forEach((v, id) => {
        const on = shown.has(id);
        Object.values(v.materials).forEach(mm => {
          if (mm.userData.base === undefined) mm.userData.base = mm.opacity;
          mm.opacity = on ? mm.userData.base : 0.25;
          mm.transparent = mm.opacity < 1;
          mm.needsUpdate = true;
        });
        v.label.material.opacity = on ? 1 : 0.3;
      });
      render();
    },
  };
}

// the page: 2D (the default, and where every change is made) or 3D, remembered in this browser
const el = document.getElementById("sf-3d");
if (el) {
  let view3d = null, starting = null;
  const KEY = "tp-shop-view";
  const floor = document.getElementById("shop-floor");
  const buttons = document.querySelectorAll(".sf-view [data-view]");
  async function show(mode) {
    floor.classList.toggle("mode-3d", mode === "3d");
    buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === mode)));
    el.hidden = mode !== "3d";
    try { localStorage.setItem(KEY, mode); } catch (e) { /* private mode: not remembered */ }
    if (mode !== "3d") return;
    if (!view3d) {
      starting = starting || start(el).catch(e => {
        el.querySelector(".sf-3d-view").textContent = t("3D view: {error}", {error: e.message});
        return null;
      });
      view3d = await starting;
      if (view3d && window.turnpilotShop) { view3d.dim(window.turnpilotShop.shown()); view3d.select(window.turnpilotShop.selected()); }
    }
    if (view3d) view3d.resize();
  }
  buttons.forEach(b => b.addEventListener("click", () => show(b.dataset.view)));
  addEventListener("tp-shop-select", e => { if (view3d) view3d.select(e.detail); });
  addEventListener("tp-shop-filter", e => { if (view3d) view3d.dim(e.detail); });
  addEventListener("tp-theme", () => {  // the colours are the page's tokens: built again in the new theme
    if (!view3d) return;
    view3d.dispose();
    el.querySelector(".sf-3d-view").innerHTML = "";
    view3d = null; starting = null;
    if (!el.hidden) show("3d");
  });
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { saved = null; }
  show(saved === "3d" ? "3d" : "2d");
}
