// Session state machine + persistence. No DOM.
//
//   upload → ready → home → navigate → move → navigate … → done → home
//
// Persisted state (localStorage):
//   { setupDone, screen, history: { "YYYY-MM-DD": true }, today: { date, done: { [spotId]: seconds } } }
// Completions are keyed by spotId within today's session, so a double tap can't count twice.

const KEY = "just-standup/v1/";

export const dateKey = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function fresh(saved, today) {
  const st = { setupDone: false, screen: "upload", history: {}, today: { date: today, done: {} }, ...saved };
  if (st.today.date !== today) st.today = { date: today, done: {} };
  if (st.setupDone && ["navigate", "move", "done"].includes(st.screen) && !Object.keys(st.today.done).length) st.screen = "home";
  return st;
}

export function reduce(st, a, pack) {
  const next = pack.stops.find((s) => !(s.spot.id in st.today.done));
  switch (a.type) {
    case "openUpload": return { ...st, screen: "upload" };
    case "finishUpload": return { ...st, screen: st.setupDone ? "home" : "ready" };
    case "enterSpace": return { ...st, setupDone: true, screen: "home" };
    case "start": return { ...st, screen: next ? "navigate" : "done" };
    case "arrive": return { ...st, screen: "move" };
    case "exit":
    case "home": return { ...st, screen: "home" };
    case "complete": {
      if (st.today.done[a.spotId] !== undefined) return st;
      const done = { ...st.today.done, [a.spotId]: Math.round(a.seconds) };
      const all = pack.stops.every((s) => s.spot.id in done);
      return {
        ...st,
        today: { ...st.today, done },
        history: all ? { ...st.history, [st.today.date]: true } : st.history,
        screen: all ? "done" : "navigate",
      };
    }
    default: throw new Error(`unknown action ${a.type}`);
  }
}

// Derived view model consumed by screens.
export function select(st, pack) {
  const stops = pack.stops.map((s) => ({ ...s, done: s.spot.id in st.today.done }));
  const current = stops.find((s) => !s.done) ?? null;
  const doneCount = stops.filter((s) => s.done).length;
  const totalSec = stops.reduce((t, s) => t + s.durationSec, 0);
  const remainingSec = stops.filter((s) => !s.done).reduce((t, s) => t + s.durationSec, 0);
  const activeSec = Object.values(st.today.done).reduce((t, x) => t + x, 0);
  return { screen: st.screen, stops, current, doneCount, total: stops.length, totalSec, remainingSec, activeSec,
    week: week(st.history, st.today.date), streak: streak(st.history, st.today.date), reminder: pack.reminder };
}

// Consecutive completed days ending today (or yesterday, if today isn't done yet).
function streak(history, today) {
  const d = new Date(`${today}T12:00:00`);
  if (!history[today]) d.setDate(d.getDate() - 1);
  let n = 0;
  while (history[dateKey(d)]) { n++; d.setDate(d.getDate() - 1); }
  return n;
}

function week(history, today) {
  const d = new Date(`${today}T12:00:00`);
  const monday = new Date(d);
  monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return ["M", "T", "W", "T", "F"].map((letter, i) => {
    const day = new Date(monday);
    day.setDate(monday.getDate() + i);
    const k = dateKey(day);
    return { letter, done: !!history[k], today: k === today };
  });
}

export function createStore(pack, { reset = false, storage = globalThis.localStorage } = {}) {
  const load = () => { try { return reset ? {} : JSON.parse(storage.getItem(KEY + pack.id)) ?? {}; } catch { return {}; } };
  const save = (s) => { try { storage.setItem(KEY + pack.id, JSON.stringify(s)); } catch { /* private mode */ } };
  let st = fresh(load(), dateKey());
  let p = pack;
  const subs = new Set();
  save(st);
  return {
    view: () => select(st, p),
    setPack(next) { p = next; subs.forEach((f) => f()); },
    dispatch(a) {
      const nx = reduce(st, a, p);
      if (nx === st) return;
      st = nx; save(st); subs.forEach((f) => f());
    },
    subscribe: (f) => (subs.add(f), () => subs.delete(f)),
  };
}
