# Just Standup (Move at Work)

A workplace movement demo: turn a familiar office into a personalized five-minute workout route.

## Run the web app

```bash
cd web && python3 -m http.server 5173     # then open http://localhost:5173
```

No build step: plain ES modules. On a phone, open the deployed URL and *Add to Home Screen*.

- `?pack=<id>` loads `web/content/<id>/` (default `demo`).
- `?reset` clears today's progress and setup state.
- Photos and videos you upload in the app stay on that device (IndexedDB).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module inputs/outputs, the content-pack contract and the session state machine.

```
pipeline/   offline: video/photos → frames → VGGT → scene.json + points.ply   (Python)
web/
  content/<id>/   content pack: scene / spots / exercises / session JSON + media
  src/            pack.js · media.js · store.js · map/ · screens/ · main.js
  tools/          scene-editor.html (place spots on a reconstruction)
docs/       TECHNICAL_DESIGN.md (中文), ARCHITECTURE.md, reference/01_UI.html
```

## Design references

- [Technical design (中文)](docs/TECHNICAL_DESIGN.md): reconstruction, neon rendering, motion demonstrations, data contracts, and demo milestones.
- [Latest approved UI — 01_UI.html](docs/reference/01_UI.html): download and open in a browser. The web app implements its five screens.
- Space reference: [clean office still](docs/media/03_space_clean_v1.png) and a [10 s pan/push video](docs/media/04_space_clean_pan_push_10s.mp4). Use them for the same-camera "Enter workout mode" demo.

## User journey

1. **Create a personal model.** Establish the user's body parameters and personalized appearance.
2. **Reconstruct the workspace.** Build a 3D office scene from photos or a video.
3. **Identify workout spots.** Identify suitable locations and objects, such as chairs, desk edges, railings, and open floor space.
4. **Prepare movement demonstrations.** Match exercises to each spot and show the personalized character performing them in that setting.
5. **Follow the map and check in.** Visit each spot, watch the demonstration, complete the movement, and light up the map until the daily session is complete.

The first four steps are primarily setup work. Daily sessions combine prepared movements into a five-minute route.

## Current status

- **Web MVP:** the five UI screens are wired to data: space ready → home → walk to stop → move video + timer → done. Progress persists, and you can upload photos and videos on the phone.
- **Map:** isometric SVG drawn from `scene.json`. Reconstructed packs overlay the `topdown.png` floor. The demo room is hand-authored, not reconstructed.
- **Pipeline:** frames → VGGT → metric-ish scene (scale assumes 1.35 m phone height unless `--scale` is given). Spots are placed by hand in the scene editor.
- **Not yet:** personal avatar, generated motion videos, a Three.js neon map in the app, cross-device sync, real reminders/notifications.

Out of scope for the demo: AR, real-time motion correction, pain assessment, and training-goal personalization.

Earlier light-theme SVG drafts remain in `outputs/` for reference.
