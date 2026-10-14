// Map seam. Screens call mountMap(el, scene, view) and get back a cleanup function.
// Today: isometric SVG. Later: a Three.js renderer (points.ply / office.glb) with the same signature.
import { renderIsoMap } from "./isoMap.js";

export function mountMap(el, scene, view) {
  el.innerHTML = renderIsoMap(scene, view);
  return () => {};
}
