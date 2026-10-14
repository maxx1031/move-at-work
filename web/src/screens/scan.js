// Setup 1/3 · Scan: a short walk-around video of the room is the input for the 3D build.
// Uses scene.capture.video (bundled sample) unless the user records/picks their own (media key "scan").
import { esc, icon, logo, segments } from "../ui.js";
import { mascot } from "../mascot.js";

export default {
  render(v, pack) {
    const c = pack.scene.capture ?? { video: "", steps: [] };  // packs without a room video still pass through
    return `<div class="screen">
  <div class="row">${logo()}<div class="mono muted s11">Setup · 1 / 3</div></div>
  ${segments(3, 0, 1)}
  <div class="up-hero"><h1 class="h1" style="font-size:44px">Scan<br><em>your space.</em></h1>${mascot({ pose: "snap", face: "wink", anim: true })}</div>
  <p class="lead">Walk slowly around the room with your phone. One short video is all it takes to rebuild it in 3D.</p>
  <div class="scanbox">
    <video class="media" src="${esc(c.video)}"${c.poster ? ` poster="${esc(c.poster)}"` : ""} autoplay muted loop playsinline></video>
    <div class="finder"></div>
    <div class="scan"></div>
    <div class="chip dark tl"><span class="dot rec"></span>${c.uploaded ? "Your room video" : "Sample room video"}</div>
  </div>
  <ul class="tips">
    <li>Move slowly and keep the floor in view</li>
    <li>Pass every chair, rail and table you want to use</li>
  </ul>
  <div class="col8 auto">
    <label class="btn ghost">${icon.video}Record or pick another video<input type="file" accept="video/*" data-scan hidden></label>
    <button class="btn primary" data-action="scanned">Build my 3D space ${icon.arrow}</button>
  </div>
</div>`;
  },

  mount(root, v, ctx) {
    root.querySelector("[data-scan]").onchange = async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      await ctx.media.put("scan", file);
      await ctx.refreshMedia();
    };
  },
};
