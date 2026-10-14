// Setup 2/3 · Build: the room video fades into the neon 3D space, which grows from the floor up while the
// processing steps tick off. For the demo pack the model was built offline, so this replays it (see capture.note).
import { esc, icon, logo, segments } from "../ui.js";

const REVEAL_S = 5;

export default {
  render(v, pack) {
    const c = pack.scene.capture ?? { video: "", steps: [] };  // packs without a room video still pass through
    const steps = c.steps.map((s) => `<li><i>${icon.check}</i><span><b>${esc(s.label)}</b><small>${esc(s.detail)}</small></span></li>`).join("");
    return `<div class="screen">
  <div class="row">${logo()}<div class="mono muted s11">Setup · 2 / 3</div></div>
  ${segments(3, 1, 0.5)}
  <h1 class="h1" style="font-size:44px">Building<br><em>your space.</em></h1>
  <div class="mapbox buildbox">
    <div data-map></div>
    <video class="media fadeout" src="${esc(c.video)}" autoplay muted playsinline></video>
    <div class="chip lime tl" data-status><span class="dot rec"></span>VIDEO → 3D</div>
    <div class="buildpct mono" data-pct>0%</div>
  </div>
  <ol class="steps">${steps}</ol>
  <p class="mono muted s11 lh16">${esc(c.note ?? "")}</p>
  <button class="btn primary" data-action="built" disabled>See my space ${icon.arrow}</button>
</div>`;
  },

  map: (v) => ({
    spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, label: s.spot.label, photo: s.spot.photo, state: "idle" })),
    route: "dashed",
    reveal: REVEAL_S,
  }),

  mount(root) {
    const items = [...root.querySelectorAll(".steps li")], total = (REVEAL_S + 1) * 1000, t0 = performance.now();
    const pct = root.querySelector("[data-pct]"), btn = root.querySelector("[data-action=built]");
    let raf = requestAnimationFrame(function tick(now) {
      const k = Math.min(1, (now - t0) / total);
      pct.textContent = `${Math.round(k * 100)}%`;
      items.forEach((li, i) => li.className = k >= 1 || k * items.length > i + 1 ? "done" : k * items.length > i ? "now" : "");
      if (k < 1) { raf = requestAnimationFrame(tick); return; }
      root.querySelector("[data-status]").innerHTML = `<span class="dot"></span>3D SPACE READY`;
      btn.disabled = false;
    });
    return () => cancelAnimationFrame(raf);
  },
};
