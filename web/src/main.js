// App shell: load pack + uploaded media → store → render the current screen.
//   ?pack=<id>   content pack under content/ (default "demo")
//   ?reset       clear today's progress and setup state
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

const base = await loadPack(params.get("pack") || "demo");
let pack = withMedia(base, await media.loadUrls());
const store = createStore(pack, { reset: params.has("reset") });
if (params.has("reset")) history.replaceState(null, "", location.pathname + (params.get("pack") ? `?pack=${params.get("pack")}` : ""));

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
store.subscribe(render);
render();
