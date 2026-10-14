// Setup step: user uploads a photo per spot and a demo video per move. Stored on-device via media.js.
import { esc, icon, logo, segments } from "../ui.js";
import { mascot } from "../mascot.js";

function row({ key, title, uploaded, sample, preview, accept, hint }) {
  return `<div class="up-row">
  <label class="up-pick">
    <span class="up-thumb">${preview || (accept.startsWith("image") ? icon.camera : icon.video)}</span>
    <span class="up-text"><b>${esc(title)}</b><small class="${uploaded ? "ok" : ""}">${uploaded ? "Added ✓ · tap to change" : sample ? "Sample included · tap to replace" : hint}</small></span>
    <input type="file" accept="${accept}" data-key="${key}" hidden>
  </label>
  ${uploaded ? `<button class="up-x" data-remove="${key}" aria-label="Remove">${icon.close}</button>` : ""}
</div>`;
}

export default {
  render(v, pack) {
    const spots = v.stops.map((s) => row({
      key: `spot:${s.spot.id}`, title: s.spot.label, uploaded: !!s.spot.uploaded, sample: !!s.spot.photo, accept: "image/*",
      hint: "Take or pick a photo of this spot",
      preview: s.spot.photo && `<img src="${esc(s.spot.photo)}" alt="">`,
    })).join("");
    const moves = v.stops.map((s) => row({
      key: `exercise:${s.exercise.id}`, title: s.exercise.name, uploaded: !!s.exercise.media?.uploaded, sample: !!s.exercise.media, accept: "video/*",
      hint: "Record or pick a short demo video",
      preview: s.exercise.media?.type === "video" ? `<video src="${esc(s.exercise.media.src)}#t=0.1" muted playsinline preload="metadata"></video>`
        : s.exercise.media?.type === "stills" ? `<img src="${esc(s.exercise.media.src[0])}" alt="">` : "",
    })).join("");
    return `<div class="screen">
  <div class="row">${logo()}<div class="mono muted s11">Photos &amp; videos</div></div>
  <div class="up-hero"><h1 class="h1" style="font-size:44px">Bring your<br><em>space in.</em></h1>${mascot({ pose: "snap", face: "wink", anim: true })}</div>
  <p class="lead">Add a photo of each spot and a short video of each move. Files stay on this phone.</p>
  <div class="scroll">
    <div class="sect">YOUR SPOTS · PHOTO</div>${spots}
    <div class="sect">YOUR MOVES · VIDEO</div>${moves}
  </div>
  <button class="btn primary" data-action="finishUpload">Save ${icon.check}</button>
</div>`;
  },

  mount(root, v, ctx) {
    root.querySelectorAll("input[data-key]").forEach((input) => {
      input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) return;
        input.closest(".up-row").classList.add("busy");
        await ctx.media.put(input.dataset.key, file);
        await ctx.refreshMedia();
      };
    });
    root.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.onclick = async () => { await ctx.media.remove(btn.dataset.remove); await ctx.refreshMedia(); };
    });
  },
};
