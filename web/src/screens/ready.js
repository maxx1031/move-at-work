// UI 01 · Space ready: overview of the rebuilt space and the spots found.
import { esc, icon, logo, nn, prime, segments } from "../ui.js";

export default {
  render(v, pack) {
    const scanned = pack.scene.origin !== "hand-authored";
    const exName = (id) => pack.exercises.find((e) => e.id === id)?.name ?? id;
    const list = v.stops.map((s) => `<div class="stop-row">
  <div class="num mono">${nn(s.n)}</div>
  <div class="grow"><div class="t15">${esc(s.spot.label)}</div><div class="muted s12">${esc(s.spot.exerciseIds.map(exName).join(" · "))}</div></div>
  <div class="mono muted s12">${prime(s.durationSec)}</div>
</div>`).join("");
    return `<div class="screen">
  <div class="row">${logo()}<div class="mono muted s11">Setup · Done</div></div>
  ${segments(2, 2)}
  <h1 class="h1" style="font-size:48px">Right here.<br><em>Your gym.</em></h1>
  <p class="lead">We found ${v.total} spots around you. Set it up once, move anytime.</p>
  <div class="mapbox">
    <div data-map></div>
    <div class="chip lime tl"><span class="dot"></span>${scanned ? "3D SPACE REBUILT" : "DEMO SPACE"}</div>
    <div class="chip ghost tr">${pack.scene.objects.length} objects</div>
    <div class="scan"></div>
  </div>
  <div class="col8">${list}</div>
  <button class="btn primary" data-action="enterSpace">Enter my space ${icon.arrow}</button>
</div>`;
  },

  map: (v) => ({
    spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, label: s.spot.label, photo: s.spot.photo, state: "idle" })),
    route: "dashed",
  }),
};
