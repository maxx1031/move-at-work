// Map seam. Screens call mountMap(el, scene, view) and get back a cleanup function.
// scene.model present -> Three.js renderer (loaded on demand); otherwise the isometric SVG.
import { renderIsoMap } from "./isoMap.js";

export function mountMap(el, scene, view) {
  if (!scene.model) {
    el.innerHTML = renderIsoMap(scene, view);
    return () => {};
  }
  el.classList.add("gl");
  let cleanup = null, dead = false;
  import("./threeMap.js").then((m) => { if (!dead) cleanup = m.mountThreeMap(el, scene, view); });
  return () => { dead = true; cleanup?.(); };
}
