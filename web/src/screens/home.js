// UI 02 · Home: today's route, progress, week strip, start.
import { clock12, esc, icon, logo, mmss, nn, prime, weekStrip } from "../ui.js";

export default {
  render(v) {
    const allDone = v.doneCount === v.total;
    const tiles = v.stops.map((s) => {
      const cls = s.done ? "done" : s === v.current ? "next" : "";
      return `<div class="tile ${cls}"><div class="mono s11">${nn(s.n)} · ${s.done ? "✓ lit" : prime(s.durationSec)}</div><div class="t14">${esc(s.title)}</div></div>`;
    }).join("");
    const cta = allDone
      ? `<button class="btn outline" data-action="start">See today's summary</button>`
      : `<button class="btn primary spread" data-action="start"><span class="row g10">${icon.play}${v.doneCount ? `Continue · stop ${v.current.n}` : `Start my ${Math.round(v.totalSec / 60)} minutes`}</span><span class="mono s14 w500">${mmss(v.remainingSec)}</span></button>`;
    return `<div class="screen">
  <div class="row">${logo()}
    <div class="row g6">
      <div class="pill mono">${icon.bell}${clock12(v.reminder)}</div>
      <button class="pill icon-only" data-action="openUpload" aria-label="Photos and videos">${icon.gear}</button>
    </div>
  </div>
  <h1 class="h1" style="font-size:56px">Stand up.<br><em>Just ${Math.round(v.totalSec / 60)} min.</em></h1>
  <p class="lead">Right here · ${allDone ? "Done today" : v.doneCount ? `${v.doneCount} of ${v.total} done` : "Not done today"}</p>
  <div class="mapbox">
    <div data-map></div>
    <div class="chip cyan tl">TODAY'S ROUTE · ${v.total} STOPS</div>
    <div class="lit-count"><b>${v.doneCount}</b><span class="mono">/ ${v.total} lit</span></div>
  </div>
  <div class="tiles">${tiles}</div>
  <div class="card row pad">
    <div><div class="t14">This week</div><div class="mono muted s11">Workdays</div></div>
    ${weekStrip(v.week)}
  </div>
  ${cta}
</div>`;
  },

  map: (v) => ({
    spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, photo: s.spot.photo, state: s.done ? "done" : s === v.current ? "next" : "idle" })),
    route: "dashed",
  }),
};
