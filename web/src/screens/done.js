// UI 05 · Done for today, Duolingo-style: celebration (confetti, mascot, counting stat tiles) → streak → summary.
// The two steps are local to this screen; only "Back to my space" goes through the store.
import { clock12, esc, icon, mmss } from "../ui.js";
import { mascot } from "../mascot.js";

const XP_PER_MIN = 10;
const DAYS = ["M", "T", "W", "T", "F", "S", "S"];

const flame = `<svg class="flame" viewBox="0 0 120 150" aria-hidden="true">
  <path d="M60 6 C72 34 104 52 104 94 C104 126 84 146 60 146 C36 146 16 126 16 94 C16 70 30 56 38 40 C42 58 50 64 56 66 C52 44 54 24 60 6Z" fill="#FF9F1C"/>
  <path d="M60 56 C68 74 86 86 86 108 C86 128 74 140 60 140 C46 140 34 128 34 108 C34 94 42 86 48 78 C50 90 56 94 60 94 C58 82 56 68 60 56Z" fill="#FFD23F"/>
</svg>`;

function tile(kind, label, iconSvg, value, count) {
  return `<div class="tile ${kind}"><div class="tile-h">${label}</div>
    <div class="tile-b">${iconSvg}<b data-count="${count.to}" data-fmt="${count.fmt}">${value}</b></div></div>`;
}

function weekRow(week) {
  return `<div class="streak-week">${week.map((d, i) => `<div class="sw ${d.done ? "on" : ""} ${d.today ? "now" : ""}" style="--i:${i}">
    <span class="mono s11">${DAYS[i]}</span><i>${d.done ? icon.check : ""}</i></div>`).join("")}</div>`;
}

// Monday-first full week; store.week gives Mon–Fri, pad the weekend (today is done when this screen shows).
function fullWeek(week) {
  const dow = (new Date().getDay() + 6) % 7;
  return [...week, ...[5, 6].map((i) => ({ done: i === dow, today: i === dow }))];
}

export default {
  render(v) {
    const xp = Math.round((v.totalSec / 60) * XP_PER_MIN);
    const streak = Math.max(1, v.streak);
    return `<div class="screen celebrate" data-step="win">
  <canvas class="confetti" data-confetti></canvas>

  <section class="step win">
    <div class="mascot-wrap"><div class="burst"></div>${mascot({ pose: "flex", anim: true })}</div>
    <h1 class="h1 pop" style="font-size:44px">Workout<br><em>complete!</em></h1>
    <p class="lead pop d1">${v.total} spots lit · ${Math.round(v.totalSec / 60)} minutes moved</p>
    <div class="tiles">
      ${tile("xp", "TOTAL XP", '<span class="ti">⚡</span>', xp, { to: xp, fmt: "n" })}
      ${tile("time", "ACTIVE", `<span class="ti">${icon.timer}</span>`, mmss(v.activeSec), { to: v.activeSec, fmt: "mmss" })}
      ${tile("spots", "SPOTS", '<span class="ti">✦</span>', `${v.doneCount}/${v.total}`, { to: v.doneCount, fmt: `of${v.total}` })}
    </div>
    <button class="btn duo" data-next>Continue</button>
  </section>

  <section class="step streak" hidden>
    <div class="mascot-wrap">${mascot({ pose: "cheer", face: "wow", anim: true })}<div class="streak-badge">${flame}<b>${streak}</b></div></div>
    <h1 class="h1 pop" style="font-size:40px"><em>${streak} day</em><br>streak!</h1>
    <p class="lead pop d1">Show up tomorrow to keep it going.</p>
    <div class="card pad col12 pop d2">${weekRow(fullWeek(v.week))}
      <div class="s13 soft">Next session · ${esc(clock12(v.reminder))} · ${Math.round(v.totalSec / 60)} min</div>
    </div>
    <div class="mapbox litbox pop d3"><div data-map></div></div>
    <button class="btn duo" data-action="home">Back to my space</button>
  </section>
</div>`;
  },

  map: (v) => ({
    spots: v.stops.map((s) => ({ position: s.spot.position, n: s.n, photo: s.spot.photo, state: s.done ? "done" : "idle" })),
    route: "lit",
    theme: "lit",
  }),

  mount(root) {
    const screen = root.querySelector(".celebrate");
    const stopConfetti = confetti(root.querySelector("[data-confetti]"));
    const timers = [];
    chime([523, 659, 784, 1047]);
    navigator.vibrate?.([30, 40, 60]);

    root.querySelectorAll("[data-count]").forEach((el, i) => {
      timers.push(setTimeout(() => countUp(el), 500 + i * 250));
    });

    root.querySelector("[data-next]").addEventListener("click", () => {
      screen.dataset.step = "streak";
      root.querySelector(".step.win").hidden = true;
      root.querySelector(".step.streak").hidden = false;
      screen.scrollTop = 0;
      chime([392, 523, 659]);
      confetti(root.querySelector("[data-confetti]"), 70);
    });

    return () => { stopConfetti(); timers.forEach(clearTimeout); };
  },
};

function countUp(el) {
  const to = +el.dataset.count, fmt = el.dataset.fmt, t0 = performance.now(), dur = 900;
  const show = (x) => fmt === "mmss" ? mmss(x) : fmt.startsWith("of") ? `${Math.round(x)}/${fmt.slice(2)}` : String(Math.round(x));
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - (1 - k) ** 3;
    el.textContent = show(to * e);
    if (k < 1) requestAnimationFrame(step); else el.parentElement.parentElement.classList.add("landed");
  };
  el.textContent = show(0);
  requestAnimationFrame(step);
}

function chime(freqs) {
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    freqs.forEach((f, i) => {
      const o = ac.createOscillator(), g = ac.createGain(), t = ac.currentTime + i * 0.09;
      o.type = "triangle"; o.frequency.value = f;
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.18, t + 0.02); g.gain.exponentialRampToValueAtTime(0.001, t + 0.35);
      o.connect(g).connect(ac.destination); o.start(t); o.stop(t + 0.4);
    });
    setTimeout(() => ac.close(), 1500);
  } catch { /* no audio */ }
}

function confetti(canvas, count = 140) {
  if (!canvas || matchMedia("(prefers-reduced-motion: reduce)").matches) return () => {};
  const ctx = canvas.getContext("2d"), dpr = Math.min(devicePixelRatio || 1, 2);
  const { width: W, height: H } = canvas.getBoundingClientRect();
  canvas.width = W * dpr; canvas.height = H * dpr; ctx.scale(dpr, dpr);
  const colors = ["#C6FF3D", "#5EE6D0", "#FFD23F", "#FF9F1C", "#FF6B9A", "#FFFFFF"];
  const parts = Array.from({ length: count }, () => ({
    x: W / 2 + (Math.random() - 0.5) * 60, y: H * 0.32,
    vx: (Math.random() - 0.5) * 11, vy: -Math.random() * 12 - 4,
    w: 5 + Math.random() * 6, h: 8 + Math.random() * 8, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
    c: colors[(Math.random() * colors.length) | 0],
  }));
  let raf, t0 = performance.now();
  const tick = (t) => {
    ctx.clearRect(0, 0, W, H);
    let alive = 0;
    for (const p of parts) {
      p.vy += 0.32; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
      if (p.y > H + 20) continue;
      alive++;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.scale(1, Math.cos(p.r * 2));
      ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore();
    }
    if (alive && t - t0 < 6000) raf = requestAnimationFrame(tick); else ctx.clearRect(0, 0, W, H);
  };
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}
