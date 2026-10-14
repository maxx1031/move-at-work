// UI 03 · To the next stop: zoomed map with the walk from you (entry or last stop) to the target.
import { esc, icon, mmss, nn, segments } from "../ui.js";

const from = (v, pack) => {
  const prev = v.stops[v.current.n - 2];
  return prev ? prev.spot.position : pack.scene.entry ?? [0, 0, 0];
};

export default {
  render(v) {
    const s = v.current;
    return `<div class="screen">
  ${segments(v.total, v.doneCount, 0.3)}
  <div class="row"><div class="kicker">TODAY · STOP ${s.n} / ${v.total}</div><div class="mono muted s11">${mmss(v.remainingSec)}</div></div>
  <h1 class="h1" style="font-size:46px">To the <em>${esc(s.spot.shortName ?? s.spot.label)}.</em></h1>
  <p class="lead">No rush. Start once you get there.</p>
  <div class="mapbox tall">
    <div data-map class="flush"></div>
    <div class="chip lime bl">${icon.nav}FOLLOW THE LIGHT</div>
  </div>
  <div class="card nav-card">
    <div class="row g14">
      ${s.spot.photo ? `<img class="nav-photo" src="${esc(s.spot.photo)}" alt="">` : `<div class="num big mono">${nn(s.n)}</div>`}
      <div class="grow"><div class="t18">${esc(s.spot.label)}</div><div class="mono muted s12">${esc(s.exercise.name)} · ${Math.round(s.durationSec / 60)} min</div></div>
    </div>
    <div class="hr"></div>
    <div class="row g10 top"><span class="cyan">${icon.info}</span><div class="s13 soft">${esc(s.spot.tip ?? "")}</div></div>
  </div>
  <button class="btn primary" data-action="arrive">I'm here · Show the move ${icon.arrow}</button>
</div>`;
  },

  map(v, pack) {
    const target = v.current.spot, start = from(v, pack);
    return {
      spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, photo: s.spot.photo,
        label: s === v.current ? s.spot.label : undefined,
        state: s === v.current ? "next" : s.done ? "done" : "dim" })),
      route: "faint",
      leg: { from: start, to: target.position },
      you: start,
      highlight: target.objectId,
      focus: [start, target.position],
    };
  },
};
