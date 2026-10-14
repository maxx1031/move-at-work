// App shell: load pack + uploaded media → store → render the current screen.
//   ?pack=<id>   content pack under content/ (default "office3d"; "demo" is the illustrated placeholder room)
//   (default)    every load starts the flow from the beginning (demo mode); uploaded media are kept
//   ?keep        keep progress and setup state across reloads
//   ?screen=<s>  open on a given screen by replaying the normal actions (used by storyboard.html)
import { loadPack, withMedia } from "./pack.js";
import * as media from "./media.js";
import { createStore } from "./store.js";
import { mountMap } from "./map/index.js";
import upload from "./screens/upload.js";
import ready from "./screens/ready.js";
import home from "./screens/home.js";
import navigate from "./screens/navigate.js";
import move from "./screens/move.js";
import done from "./screens/done.js";

const SCREENS = { upload, ready, home, navigate, move, done };
const params = new URLSearchParams(location.search);
const root = document.getElementById("app");

const base = await loadPack(params.get("pack") || "office3d");
let pack = withMedia(base, await media.loadUrls());
const store = createStore(pack, { reset: !params.has("keep") });

const ctx = {
  media,
  dispatch: store.dispatch,
  async refreshMedia() { pack = withMedia(base, await media.loadUrls()); store.setPack(pack); },
};

let cleanup = [];
function render() {
  cleanup.forEach((f) => f());
  const v = store.view();
  const screen = SCREENS[v.screen];
  root.innerHTML = screen.render(v, pack);
  root.scrollTop = 0;
  const mapEl = root.querySelector("[data-map]");
  cleanup = [
    mapEl && screen.map ? mountMap(mapEl, pack.scene, screen.map(v, pack)) : null,
    screen.mount?.(root, v, ctx),
  ].filter(Boolean);
}

root.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (el) store.dispatch({ type: el.dataset.action });
});
// Replays the real flow up to a screen, so the state machine stays the only way to change state.
function jumpTo(screen) {
  const steps = ["finishUpload", "enterSpace", "start", "arrive"];
  const n = { upload: 0, ready: 1, home: 2, navigate: 3, move: 4, done: 2 }[screen] ?? 0;
  steps.slice(0, n).forEach((type) => store.dispatch({ type }));
  if (screen === "done") pack.stops.forEach((s) => store.dispatch({ type: "complete", spotId: s.spot.id, seconds: s.durationSec }));
}
if (params.has("screen")) jumpTo(params.get("screen"));

store.subscribe(render);
render();
