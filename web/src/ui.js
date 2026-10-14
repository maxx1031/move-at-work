// Shared template helpers. Pure functions returning HTML strings.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
export const pad2 = (n) => String(n).padStart(2, "0");
export const mmss = (sec) => `${pad2(Math.floor(sec / 60))}:${pad2(Math.floor(sec % 60))}`;
export const prime = (sec) => `${Math.floor(sec / 60)}′${pad2(sec % 60)}`;
export const nn = (n) => pad2(n);

export function clock12(hhmm = "15:00") {
  const [h, m] = hhmm.split(":").map(Number);
  return `${((h + 11) % 12) + 1}:${pad2(m)} ${h < 12 ? "AM" : "PM"}`;
}

const svg = (d, { fill = "none", sw = 2, size = 18 } = {}) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}" stroke="${fill === "none" ? "currentColor" : "none"}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;

export const icon = {
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>', { sw: 2.2 }),
  play: svg('<path d="M7 4.5v15l13-7.5z"/>', { fill: "currentColor" }),
  pause: svg('<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>', { fill: "currentColor", size: 16 }),
  check: svg('<path d="M5 12l5 5L20 7"/>', { sw: 2.6 }),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>', { sw: 2.2, size: 16 }),
  bell: svg('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>', { size: 13 }),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>', { size: 16 }),
  nav: svg('<path d="M12 2l7 19-7-4-7 4z"/>', { sw: 2.4, size: 11 }),
  timer: svg('<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M9 2h6"/>'),
  sound: svg('<path d="M11 5L6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>', { size: 16 }),
  muted: svg('<path d="M11 5L6 9H3v6h3l5 4z"/><path d="M22 9l-6 6M16 9l6 6"/>', { size: 16 }),
  loop: svg('<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>', { sw: 2.2, size: 15 }),
  camera: svg('<path d="M3 8h4l2-3h6l2 3h4v11H3z"/><circle cx="12" cy="13" r="3.5"/>', { size: 16 }),
  video: svg('<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/>', { size: 16 }),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 13.6H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>', { size: 15 }),
};

export const logo = () => `<div class="logo">JUST STANDUP</div>`;

// Segmented progress: one segment per stop; `fill` is 0..1 for the active segment.
export function segments(total, doneCount, fill = 0) {
  return `<div class="segs">${Array.from({ length: total }, (_, i) => {
    const w = i < doneCount ? 100 : i === doneCount ? Math.round(fill * 100) : 0;
    return `<i><b style="width:${w}%"></b></i>`;
  }).join("")}</div>`;
}

export function weekStrip(week) {
  return `<div class="week">${week.map((d) =>
    `<span class="${d.done ? "on" : ""} ${d.today ? "now" : ""}">${d.letter}</span>`).join("")}</div>`;
}
