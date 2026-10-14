// Isometric SVG office map. Pure: (scene, view) -> SVG markup.
//
// scene: scene.json (app space: meters, Y up, floor y=0). Uses bounds, wallHeight, objects[], topdown?
// view:  {
//   spots:     [{ position:[x,y,z], n, label?, photo?, state: "idle"|"next"|"done"|"dim" }],
//   route:     "dashed" | "lit" | "faint" | null    — path through spots in order
//   leg:       { from:[x,z], to:[x,z] } | null       — highlighted walk to the next stop
//   you:       [x,z] | null
//   highlight: objectId | null
//   focus:     [[x,z], ...] | null                   — zoom the viewBox onto these points
//   theme:     "cyan" | "lit"
// }

import { esc } from "../ui.js";

const LIME = "#C6FF3D", CYAN = "#5EE6D0";
const VB = [0, 0, 340, 250];
const f = ([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`;

// World (x, z, height) -> SVG px. Floor diamond fits a 300px wide box like the UI drawing.
export function projector(scene) {
  const [x0, , z0] = scene.bounds.min, [x1, , z1] = scene.bounds.max;
  const a = 300 / (x1 - x0 + (z1 - z0)), b = a * 0.533, c = a * 0.96;
  const H = scene.wallHeight ?? 2.5;
  const ox = 20 + (z1 - z0) * a, oy = 10 + H * c;
  const p = (x, z, y = 0) => [ox + (x - x0) * a - (z - z0) * a, oy + (x - x0) * b + (z - z0) * b - y * c];
  return { p, a, b, c, x0, z0, x1, z1, H };
}

function room(P, tint) {
  const { p, x0, z0, x1, z1, H } = P;
  const poly = (pts, attrs) => `<polygon points="${pts.map(f).join(" ")}" ${attrs}/>`;
  const stroke = tint === LIME ? "rgba(198,255,61," : "rgba(94,230,208,";
  let s = poly([p(x0, z1), p(x0, z0), p(x0, z0, H), p(x0, z1, H)], `fill="url(#wl)" stroke="${stroke}.3)"`);
  s += poly([p(x0, z0), p(x1, z0), p(x1, z0, H), p(x0, z0, H)], `fill="url(#wl)" stroke="${stroke}.3)"`);
  const floor = [p(x0, z0), p(x1, z0), p(x1, z1), p(x0, z1)];
  s += poly(floor, `fill="#111816" stroke="${stroke}.55)"`) + poly(floor, `fill="url(#fl)"`);
  let g = "";
  for (const k of [0.25, 0.5, 0.75]) {
    const x = x0 + (x1 - x0) * k, z = z0 + (z1 - z0) * k;
    g += `<line x1="${p(x, z0)[0]}" y1="${p(x, z0)[1]}" x2="${p(x, z1)[0]}" y2="${p(x, z1)[1]}"/>`;
    g += `<line x1="${p(x0, z)[0]}" y1="${p(x0, z)[1]}" x2="${p(x1, z)[0]}" y2="${p(x1, z)[1]}"/>`;
  }
  return s + `<g stroke="${stroke}.10)">${g}</g>`;
}

// Reconstructed top-down render (build_scene.py) laid onto the floor with an affine transform.
function floorImage(P, td) {
  if (!td) return "";
  const { p, a, b } = P, k = td.cellMeters;
  const [e, fy] = p(td.originXZ[0], td.originXZ[1]);
  return `<image href="${esc(td.href)}" width="${td.sizePx[0]}" height="${td.sizePx[1]}" opacity=".85"
    transform="matrix(${k * a} ${k * b} ${-k * a} ${k * b} ${e} ${fy})" preserveAspectRatio="none"/>`;
}

function box(P, o, hot) {
  const { p } = P;
  const [cx, y0, cz] = o.position, [sx, sy, sz] = o.size;
  const xa = cx - sx / 2, xb = cx + sx / 2, za = cz - sz / 2, zb = cz + sz / 2, y1 = y0 + sy;
  const poly = (pts, fill, stroke) => `<polygon points="${pts.map(f).join(" ")}" fill="${fill}" stroke="${stroke}"/>`;
  const glass = o.kind === "window" || o.kind === "monitor";
  const line = hot ? LIME : glass ? "rgba(94,230,208,.8)" : "rgba(94,230,208,.6)";
  const side = glass ? "rgba(94,230,208,.18)" : "#1A2321";
  return poly([p(xa, zb, y0), p(xb, zb, y0), p(xb, zb, y1), p(xa, zb, y1)], side, line) +
    poly([p(xb, za, y0), p(xb, zb, y0), p(xb, zb, y1), p(xb, za, y1)], glass ? side : "#141C1A", line) +
    poly([p(xa, za, y1), p(xb, za, y1), p(xb, zb, y1), p(xa, zb, y1)], hot ? "rgba(198,255,61,.18)" : glass ? side : "#22302C", line);
}

// Catmull-Rom through points, as cubic Béziers.
function smooth(pts) {
  let d = `M${f(pts[0])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${f(c1)} ${f(c2)} ${f(p2)}`;
  }
  return d;
}

function route(pts, style) {
  if (!style || pts.length < 2) return "";
  const d = smooth(pts);
  if (style === "lit") return `<path class="glow" d="${d}" fill="none" stroke="${LIME}" stroke-width="2.2"/><path class="flow" d="${d}" fill="none" stroke="#F2FFD0" stroke-width="2.2"/>`;
  if (style === "faint") return `<path d="${d}" fill="none" stroke="${CYAN}" stroke-width="1.2" stroke-opacity=".25" stroke-dasharray="2 5"/>`;
  return `<path class="route" d="${d}" fill="none" stroke="${CYAN}" stroke-width="1.6" stroke-opacity=".7"/>`;
}

function leg(a, b) {
  const mid = [(a[0] + b[0]) / 2 - (b[1] - a[1]) * 0.25, (a[1] + b[1]) / 2 + (b[0] - a[0]) * 0.25];
  return `<path class="route glow" d="M${f(a)} Q${f(mid)} ${f(b)}" fill="none" stroke="${LIME}" stroke-width="2"/>`;
}

function marker(s, [x, y]) {
  const g = (body, extra = "") => `<g transform="translate(${x.toFixed(1)},${y.toFixed(1)})"${extra}>${body}</g>`;
  if (s.state === "dim") return g(`<ellipse rx="12" ry="6" fill="rgba(94,230,208,.10)" stroke="rgba(94,230,208,.6)"/>`, ` opacity=".5"`);
  const lit = s.state !== "idle";
  const col = lit ? LIME : CYAN;
  let body = s.state === "next" ? `<ellipse class="pulse" rx="16" ry="8" fill="none" stroke="${LIME}"/>` : "";
  body += `<ellipse rx="16" ry="8" fill="${lit ? "rgba(198,255,61,.14)" : "rgba(94,230,208,.08)"}" stroke="${lit ? LIME : "rgba(94,230,208,.5)"}"/>`;
  body += `<line x1="0" y1="0" x2="0" y2="-13" stroke="${lit ? LIME : "rgba(94,230,208,.6)"}"/>`;
  if (s.photo) {
    body += `<clipPath id="pc${s.n}"><circle cy="-26" r="13"/></clipPath>
      <circle cy="-26" r="14.5" fill="${col}"/><image href="${esc(s.photo)}" x="-13" y="-39" width="26" height="26" clip-path="url(#pc${s.n})" preserveAspectRatio="xMidYMid slice"/>`;
    if (s.state === "done") body += `<circle cy="-26" r="13" fill="rgba(198,255,61,.55)"/><path d="M-4.5,-26 l3,3 l6,-6" fill="none" stroke="#0B0F0E" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`;
  } else if (s.state === "done") {
    body += `<circle class="glow" cy="-24" r="11" fill="${LIME}"/><path d="M-4.5,-24 l3,3 l6,-6" fill="none" stroke="#0B0F0E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
  } else {
    body += `<circle cy="-24" r="11" fill="#0B0F0E" stroke="${col}" stroke-width="1.5"/><text y="-20.5" text-anchor="middle" font-size="10" fill="${col}" font-family="JetBrains Mono, monospace">${s.n}</text>`;
  }
  if (s.label) {
    const w = s.label.length * 5.6 + 18;
    body += `<rect x="${-w / 2}" y="${s.photo ? -64 : -60}" width="${w}" height="18" rx="9" fill="rgba(11,15,14,.9)" stroke="rgba(94,230,208,.4)"/>
      <text y="${s.photo ? -51.5 : -47.5}" text-anchor="middle" font-size="10" fill="#EDF3EF" font-family="Space Grotesk, sans-serif">${esc(s.label)}</text>`;
  }
  return g(body);
}

const you = ([x, y]) => `<g transform="translate(${x.toFixed(1)},${y.toFixed(1)})">
  <circle class="pulse" r="5" fill="none" stroke="${CYAN}"/><circle r="4.5" fill="${CYAN}" stroke="#0B0F0E" stroke-width="2"/>
  <rect x="8" y="-8" width="28" height="16" rx="8" fill="rgba(11,15,14,.9)" stroke="rgba(94,230,208,.5)"/>
  <text x="22" y="3.5" text-anchor="middle" font-size="9" fill="${CYAN}" font-family="Space Grotesk, sans-serif">You</text></g>`;

const SPARKLES = [[60, 60, 1], [290, 40, 0.75], [300, 200, 1], [40, 210, 0.75]].map(([x, y, k], i) =>
  `<path class="twinkle" style="animation-delay:${i * 0.5}s" fill="${LIME}" d="M${x},${y} l${2 * k},${6 * k} l${6 * k},${2 * k} l${-6 * k},${2 * k} l${-2 * k},${6 * k} l${-2 * k},${-6 * k} l${-6 * k},${-2 * k} l${6 * k},${-2 * k}z"/>`).join("");

function fitBox(pts, pad = 64, aspect = 1.3) {
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  let [x0, x1, y0, y1] = [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad / 2];
  let w = Math.max(x1 - x0, 200), h = y1 - y0;
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return [cx - w / 2, cy - h / 2, w, h];
}

export function renderIsoMap(scene, view) {
  const P = projector(scene);
  const tint = view.theme === "lit" ? LIME : CYAN;
  const at = (xz) => P.p(xz[0], xz[xz.length - 1]);  // accepts [x,z] or [x,y,z]
  const spotPts = view.spots.map((s) => at(s.position));
  const objects = [...scene.objects]
    .sort((o, q) => o.position[1] - q.position[1] || (o.position[0] + o.position[2]) - (q.position[0] + q.position[2]))
    .map((o) => box(P, o, o.id === view.highlight)).join("");
  const vb = view.focus ? fitBox(view.focus.map(at)) : VB;
  return `<svg viewBox="${vb.map((v) => v.toFixed(1)).join(" ")}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
<defs>
  <linearGradient id="wl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${tint}" stop-opacity=".12"/><stop offset="1" stop-color="${tint}" stop-opacity="0"/></linearGradient>
  <radialGradient id="fl" cx=".5" cy=".5" r=".6"><stop offset="0" stop-color="${LIME}" stop-opacity="${view.theme === "lit" ? 0.18 : 0.1}"/><stop offset="1" stop-color="${LIME}" stop-opacity="0"/></radialGradient>
</defs>
${room(P, tint)}${floorImage(P, scene.topdown)}
${route(spotPts, view.route)}
${objects}
${view.leg ? leg(at(view.leg.from), at(view.leg.to)) : ""}
${view.spots.map((s, i) => marker(s, spotPts[i])).join("")}
${view.you ? you(at(view.you)) : ""}
${view.theme === "lit" ? SPARKLES : ""}
</svg>`;
}
