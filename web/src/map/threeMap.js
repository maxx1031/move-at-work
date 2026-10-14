// Three.js map for packs whose scene.json has a `model` (a module exporting build(THREE) -> Group).
// Same contract as isoMap: mountThreeMap(el, scene, view) -> cleanup. View fields: see isoMap.js, plus
//   reveal: seconds — the model grows from the floor up (clipping plane + scan plane), labels appear at the end.
// Look and camera follow final_rendering_effect.html (neon scene V5).
import * as THREE from "three";

const CYAN = 0x5ee6d0, LIME = 0xc6ff3d;
const models = new Map();  // absolute model URL -> Promise<Group>
let gl = null;             // one renderer/scene shared by every mount

function shared() {
  if (gl) return gl;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight("#b3e7d3", 1.8));
  const sun = new THREE.DirectionalLight("#b1ffe2", 2);
  sun.position.set(1, 12, 6);
  scene.add(sun);
  const overlay = new THREE.Group();
  scene.add(overlay);
  return (gl = { renderer, scene, overlay, camera: new THREE.OrthographicCamera(-10, 10, 8, -8, 0.1, 150), model: null });
}

function loadModel(src) {
  const url = new URL(src, document.baseURI).href;
  if (!models.has(url)) models.set(url, import(url).then((m) => m.build(THREE)));
  return models.get(url);
}

// Recolour the model's cyan lines: lime everywhere when lit, lime inside the highlighted object otherwise.
function tint(model, view) {
  const hot = new Set();
  model.getObjectByName(view.highlight ?? "\0")?.traverse((o) => hot.add(o));
  model.traverse((o) => {
    if (!o.material?.color || !(o.isLine || o.material.isMeshBasicMaterial)) return;
    o.material.userData.base ??= o.material.color.getHex();
    const base = o.material.userData.base;
    o.material.color.setHex(base === CYAN && (view.theme === "lit" || hot.has(o)) ? LIME : base);
  });
}

const mat = (color, opacity = 1) => new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, side: THREE.DoubleSide, depthWrite: opacity >= 1 });
const flat = (geo, m, y = 0) => { const o = new THREE.Mesh(geo, m); o.rotation.x = -Math.PI / 2; o.position.y = y; return o; };
const floorPt = (p) => new THREE.Vector3(p[0], 0.06, p[p.length - 1]);

function spotMarker(s) {
  const lit = s.state !== "idle" && s.state !== "dim";
  const color = lit ? LIME : CYAN, op = s.state === "dim" ? 0.3 : 0.85;
  const g = new THREE.Group();
  g.position.set(...s.position);
  for (const r of [0.34, 0.46]) g.add(flat(new THREE.RingGeometry(r, r + 0.018, 64), mat(color, op)));
  g.add(flat(new THREE.CircleGeometry(0.34, 48), mat(LIME, s.state === "done" ? 0.45 : 0.08)));
  if (s.position[1] > 0.1) {  // stem down to the floor for raised spots (seat, rail, table)
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, s.position[1]), mat(color, op * 0.6));
    stem.position.y = -s.position[1] / 2;
    g.add(stem);
  }
  g.userData.pulse = s.state === "next";
  return g;
}

function tube(points, color, opacity, r = 0.02) {
  if (points.length < 2) return null;
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal");
  const m = new THREE.Mesh(new THREE.TubeGeometry(curve, points.length * 24, r, 6, false), mat(color, opacity));
  m.userData.curve = curve;
  return m;
}

function labelHtml(s) {
  const img = s.photo ? `<img src="${s.photo}" alt="">` : "";
  return `${img}<b>${String(s.n).padStart(2, "0")}</b>${s.label ?? ""}`;
}

export function mountThreeMap(el, sceneDoc, view) {
  const T = shared(), cam = sceneDoc.model.camera ?? {};
  const { renderer, camera, overlay } = T;

  // --- overlay: spots, route, leg, you ---
  overlay.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
  overlay.clear();
  const markers = view.spots.map(spotMarker);
  markers.forEach((m) => overlay.add(m));
  const routeStyle = { dashed: [CYAN, 0.55], faint: [CYAN, 0.18], lit: [LIME, 0.95] }[view.route];
  const route = routeStyle && tube(view.spots.map((s) => floorPt(s.position)), ...routeStyle, 0.014);
  const leg = view.leg && tube([floorPt(view.leg.from), floorPt([(view.leg.from[0] + view.leg.to[0]) / 2 + 0.3, 0, (view.leg.from[2] + view.leg.to[2]) / 2]), floorPt(view.leg.to)], LIME, 1, 0.03);
  [route, leg].forEach((t) => t && overlay.add(t));
  const mover = leg ?? (view.route === "lit" || view.route === "dashed" ? route : null);
  const particle = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 12), mat(LIME));
  particle.visible = !!mover;
  overlay.add(particle);
  if (view.you) {
    const you = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 16), mat(CYAN));
    const ring = flat(new THREE.RingGeometry(0.2, 0.23, 48), mat(CYAN, 0.8));
    you.position.copy(floorPt(view.you)).setY(0.12);
    ring.position.copy(floorPt(view.you)).setY(0.02);
    overlay.add(you, ring);
  }

  // --- HTML labels ---
  const labels = document.createElement("div");
  labels.className = "map-labels";
  const tags = view.spots.map((s) => {
    const d = document.createElement("div");
    d.className = `map-label ${s.state}`;
    d.innerHTML = labelHtml(s);
    labels.appendChild(d);
    return d;
  });
  const youTag = view.you && Object.assign(document.createElement("div"), { className: "map-label you", textContent: "You" });
  if (youTag) labels.appendChild(youTag);
  el.append(renderer.domElement, labels);

  // --- model (cached; first mount waits for it) ---
  let dead = false, shownAt = null;
  loadModel(sceneDoc.model.href).then((model) => {
    if (dead) return;
    shownAt = performance.now();
    if (T.model !== model) { if (T.model) T.scene.remove(T.model); T.scene.add(model); T.model = model; }
    tint(model, view);
    for (const name of sceneDoc.model.hideInTopView ?? []) {
      const o = model.getObjectByName(name);
      if (o) o.visible = !view.focus;  // zoomed walk view: drop tall occluders
    }
  });

  // --- camera ---
  let yaw = cam.yaw ?? -0.28, pitch = view.focus ? 1.2 : cam.pitch ?? 0.95, interacted = false;
  const target = new THREE.Vector3(0, 0.1, 0);
  let halfW = cam.halfWidth ?? 7.6, halfH = cam.halfHeight ?? 5.2;
  if (view.focus) {
    const a = floorPt(view.focus[0]), b = floorPt(view.focus[view.focus.length - 1]);
    target.copy(a).add(b).multiplyScalar(0.5);
    halfW = Math.max(a.distanceTo(b) * 0.85, 2.8);
    halfH = halfW * 0.7;
  }
  const resize = () => {
    const w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    const aspect = w / h, span = Math.max(halfH, halfW / aspect);
    Object.assign(camera, { left: -span * aspect, right: span * aspect, top: span, bottom: -span });
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(el);
  resize();

  // --- drag to orbit ---
  const cv = renderer.domElement;
  let drag = null;
  cv.onpointerdown = (e) => { drag = [e.clientX, e.clientY]; interacted = true; cv.setPointerCapture(e.pointerId); };
  cv.onpointermove = (e) => {
    if (!drag) return;
    yaw -= (e.clientX - drag[0]) * 0.006;
    pitch = Math.max(0.35, Math.min(1.5, pitch + (e.clientY - drag[1]) * 0.005));
    drag = [e.clientX, e.clientY];
  };
  cv.onpointerup = cv.onpointercancel = () => (drag = null);

  // --- reveal: clip everything above a rising height ---
  const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
  const sweep = flat(new THREE.PlaneGeometry(13, 10), mat(LIME, 0.07));
  sweep.visible = !!view.reveal;
  overlay.add(sweep);
  renderer.clippingPlanes = view.reveal ? [clip] : [];
  labels.style.opacity = view.reveal ? 0 : 1;

  // --- loop ---
  const t0 = performance.now(), v = new THREE.Vector3();
  let raf = 0;
  const frame = (now) => {
    raf = requestAnimationFrame(frame);
    const t = Math.max(0, now - t0) / 1000;  // rAF timestamps can precede t0 on the first frame
    const y = !interacted && !view.focus ? (cam.yaw ?? -0.28) + Math.sin(t * 0.12) * 0.1 : yaw;
    if (!interacted) yaw = y;
    camera.position.set(20 * Math.sin(y) * Math.cos(pitch), 20 * Math.sin(pitch), 20 * Math.cos(y) * Math.cos(pitch)).add(target);
    camera.lookAt(target);
    markers.forEach((m, i) => m.scale.setScalar(m.userData.pulse ? 1 + Math.sin(t * 3) * 0.12 : 1 + Math.sin(t * 2 + i) * 0.04));
    if (mover) particle.position.copy(mover.userData.curve.getPoint((t * (leg ? 0.25 : 0.09)) % 1));
    if (view.reveal) {
      const k = shownAt ? Math.min(1, (now - shownAt) / 1000 / view.reveal) : 0;
      clip.constant = k < 1 ? -0.05 + k * 3.6 : 100;
      sweep.position.y = clip.constant - 0.02;
      sweep.visible = k < 1;
      labels.style.opacity = k < 1 ? 0 : 1;
    }
    renderer.render(T.scene, camera);
    const place = (tag, p, lift) => {
      v.set(p[0], (p.length > 2 ? p[1] : 0) + lift, p[p.length - 1]).project(camera);
      tag.style.transform = `translate(${(v.x * 0.5 + 0.5) * el.clientWidth}px, ${(-v.y * 0.5 + 0.5) * el.clientHeight}px) translate(-50%, -100%)`;
    };
    tags.forEach((tag, i) => place(tag, view.spots[i].position, 0.6));
    if (youTag) place(youTag, [view.you[0], 0, view.you[view.you.length - 1]], 0.45);
  };
  raf = requestAnimationFrame(frame);

  return () => {
    dead = true;
    cancelAnimationFrame(raf);
    ro.disconnect();
    renderer.clippingPlanes = [];
    cv.onpointerdown = cv.onpointermove = cv.onpointerup = cv.onpointercancel = null;
    labels.remove();
    cv.remove();
  };
}
