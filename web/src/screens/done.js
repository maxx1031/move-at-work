// UI 05 · Done for today: everything lit, stats, week strip.
import { clock12, logo, mmss, segments, weekStrip } from "../ui.js";

export default {
  render(v) {
    return `<div class="screen lit">
  ${segments(v.total, v.total)}
  <div class="kicker">TODAY · ${v.doneCount} / ${v.total}</div>
  <h1 class="h1" style="font-size:54px">${Math.round(v.totalSec / 60)} minutes.<br><em>All yours.</em></h1>
  <p class="lead">Every spot is lit for today.</p>
  <div class="mapbox litbox"><div data-map></div></div>
  <div class="stats">
    <div class="card pad16"><div class="stat">${mmss(v.activeSec)}</div><div class="mono muted s11">Active time</div></div>
    <div class="card pad16"><div class="stat lime">${v.doneCount} / ${v.total}</div><div class="mono muted s11">Spots done</div></div>
  </div>
  <div class="card pad col12">
    ${weekStrip(v.week)}
    <div class="s13 soft">See you tomorrow at ${clock12(v.reminder)} for ${Math.round(v.totalSec / 60)} more.</div>
  </div>
  <button class="btn outline" data-action="home">Back to my space</button>
</div>`;
  },

  map: (v) => ({
    spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, photo: s.spot.photo, state: s.done ? "done" : "idle" })),
    route: "lit",
    theme: "lit",
  }),
};
