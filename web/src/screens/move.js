// UI 04 · Move: demo media (video, still poses that swap, illustration, or placeholder) + a training timer.
// The timer is independent of the video: a short clip loops while the move lasts durationSec.
import { esc, icon, mmss, nn, segments } from "../ui.js";

function media(m, ex) {
  if (m?.type === "video") return `<video class="media" src="${esc(m.src)}" autoplay muted loop playsinline></video>
    <button class="round tr" data-mute aria-label="Sound">${icon.muted}</button>
    <div class="vbar">
      <button class="vbtn" data-play aria-label="Pause video">${icon.pause}</button>
      <div class="vtrack"><b data-vfill></b></div>
      <div class="mono s11 soft" data-vtime>0:00</div>
      <button class="vbtn loop on" data-loop aria-label="Loop">${icon.loop}</button>
    </div>`;
  if (m?.type === "image") return `<img class="media" src="${esc(m.src)}" alt="">`;
  if (m?.type === "stills") return `<div class="media stills">${m.src.map((s) => `<img src="${esc(s)}" alt="">`).join("")}</div>`;
  return `<div class="media placeholder"><div class="h1" style="font-size:30px">${esc(ex.name)}</div><div class="muted s13">No demo video yet · add one in setup</div></div>`;
}

export default {
  render(v) {
    const s = v.current, m = s.exercise.media;
    return `<div class="screen plain">
  <div class="row g12">
    <button class="sq" data-action="exit" aria-label="End today's session">${icon.close}</button>
    <div class="grow">${segments(v.total, v.doneCount, 0)}</div>
  </div>
  <div class="col4">
    <div class="kicker">${nn(s.n)} · ${esc(s.spot.label.toUpperCase())} · ${esc(s.exercise.name.toUpperCase())}</div>
    <h1 class="h1" style="font-size:40px">Like <em>this.</em></h1>
  </div>
  <div class="mediabox">
    ${media(m, s.exercise)}
    <div class="chip dark tl"><span class="dot rec"></span>${esc(m?.caption ?? "Placeholder")}</div>
  </div>
  <div class="row bottom">
    <div class="bigtime" data-elapsed>00:00</div>
    <div class="col4 end"><div class="mono muted s12">/ ${mmss(s.durationSec)}</div><div class="mono cyan s12" data-left></div></div>
  </div>
  <div class="bar"><b data-tfill></b></div>
  <p class="s14 soft lh16">${esc(s.exercise.cue)}</p>
  <div class="row g12 auto">
    <button class="sq big" data-timer aria-label="Pause timer">${icon.timer}</button>
    <button class="btn primary grow" data-complete>${icon.check}Done with this move</button>
  </div>
</div>`;
  },

  mount(root, v, { dispatch }) {
    const s = v.current, $ = (sel) => root.querySelector(sel);
    let acc = 0, since = performance.now(), running = true;
    const elapsed = () => acc + (running ? (performance.now() - since) / 1000 : 0);

    const tick = () => {
      const t = elapsed(), left = s.durationSec - t;
      $("[data-elapsed]").textContent = mmss(t);
      $("[data-left]").textContent = left > 0 ? `${Math.ceil(left)} s left` : "Time's up · tap done";
      $("[data-tfill]").style.width = `${Math.min(100, (t / s.durationSec) * 100)}%`;
      const seg = root.querySelectorAll(".segs b")[v.doneCount];
      if (seg) seg.style.width = `${Math.min(100, (t / s.durationSec) * 100)}%`;
      const vid = $("video.media");
      if (vid?.duration) {
        $("[data-vfill]").style.width = `${(vid.currentTime / vid.duration) * 100}%`;
        $("[data-vtime]").textContent = `${mmss(vid.currentTime).slice(1)} / ${mmss(vid.duration).slice(1)}`;
      }
    };
    const timer = setInterval(tick, 250);
    tick();

    $("[data-timer]").onclick = (e) => {
      if (running) acc = elapsed(); else since = performance.now();
      running = !running;
      e.currentTarget.classList.toggle("on", !running);
    };
    $("[data-complete]").onclick = () => dispatch({ type: "complete", spotId: s.spot.id, seconds: elapsed() });

    const vid = $("video.media");
    if (vid) {
      $("[data-mute]").onclick = (e) => { vid.muted = !vid.muted; e.currentTarget.innerHTML = vid.muted ? icon.muted : icon.sound; };
      $("[data-play]").onclick = (e) => { vid.paused ? vid.play() : vid.pause(); e.currentTarget.innerHTML = vid.paused ? icon.play : icon.pause; };
      $("[data-loop]").onclick = (e) => { vid.loop = !vid.loop; e.currentTarget.classList.toggle("on", vid.loop); };
    }
    return () => clearInterval(timer);
  },
};
