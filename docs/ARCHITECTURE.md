# Architecture

Two halves joined by one folder format, the **content pack** (`web/content/<id>/`).

```
  OFFLINE (Python, GPU/MPS)                         CONTENT PACK                       WEB APP (static, phone)
  ───────────────────────────                       ─────────────                      ───────────────────────
  video / photos                                    scene.json   ◀── build_scene.py
   │ prepare_images.py                              points.ply, topdown.png, photos/
   ▼                                                spots.json   ◀── tools/scene-editor.html (manual)
  data/<id>/images/ + frames_manifest.json          exercises.json ◀── hand-authored
   │ run_vggt.py                                    session.json   ◀── hand-authored
   ▼                                                media/*         ◀── offline renders (later)
  data/<id>/vggt/predictions.npz                         │
   │ build_scene.py                                      ▼
   ▼                                                pack.js ─▶ store.js ─▶ screens/* ─▶ map/
  web/content/<id>/scene.json, points.ply …                    ▲
                                                    media.js (photos/videos uploaded on the phone, IndexedDB)
```

## Offline pipeline (`pipeline/`)

Each step is a CLI: one folder in, files out. Steps never import each other (only the shared `plyio.py`).

| Step | Command | In | Out |
|---|---|---|---|
| 1 frames | `prepare_images.py --input <video\|dir> --scene data/<id>` | phone video or photos | `images/*.jpg`, `frames_manifest.json` |
| 2 geometry | `run_vggt.py --scene data/<id>` | `images/` + VGGT weights | `vggt/predictions.npz`, `points_raw.ply`, `run_meta.json` |
| 3 scene | `build_scene.py --scene data/<id> --out web/content/<id>` | `vggt/predictions.npz` | `scene.json`, `points.ply`, `topdown.png`, `photos/` |
| 4 spots | open `web/tools/scene-editor.html?pack=<id>` | `scene.json`, `points.ply` | `spots.json` (export button) |
| 5 course | edit JSON (copy from `content/demo`) | spot ids | `exercises.json`, `session.json` |

Not built yet, with their slots reserved in the contracts: the personal avatar (step 1 of the product) and
rendered motion videos (`exercises[].media`). Until then, users upload their own clips in the app.

## Content pack contract

All coordinates: meters, right-handed, **Y up, floor at y = 0**, the same frame for scene, spots and cameras.

| File | Key fields | Producer |
|---|---|---|
| `scene.json` | `sceneId`, `origin` (`"vggt"` \| `"modeled"` \| `"hand-authored"`), `model?{src,camera,hideInTopView,objects}`, `bounds{min,max}`, `wallHeight`, `entry[x,y,z]`, `objects[{id,kind,position(bottom-centre),size}]`, `topdown{file,cellMeters,originXZ,sizePx}`, `cameras[]` | build_scene.py / hand |
| `spots.json` | `spots[{id,label,shortName,objectId,position,headingRadians,clearanceRadius,reviewed,exerciseIds,tip,photo?}]` | scene editor |
| `scene.json` → `capture` (optional) | `{video, poster, steps[{label,detail}], note}` — the room video and the build steps shown in setup | hand / pipeline |
| `model.js` (optional) | `export function build(THREE) → Group`; child groups named by object id | hand-modeled (e.g. neon scene V5) |
| `exercises.json` | `exercises[{id,name,cue,durationSec,media?{type:"video"\|"image",src,caption}}]` | hand / renderer |
| `session.json` | `id`, `reminder "HH:MM"`, `stops[{spotId,exerciseId,title,durationSec}]` (route order) | hand |

`content/office3d` (default) is the stylised Three.js model of the real office from `final_rendering_effect.html`, with real-scene move videos.
`content/demo` is the illustrated placeholder room traced from `01_UI.html`. Other packs are local only (gitignored).

## Web app (`web/`, no build step)

| Module | In → Out | Notes |
|---|---|---|
| `src/pack.js` | `content/<id>/*.json` → `Pack` | The only fetch. Joins session stops with spots and exercises; `withMedia()` overlays uploads. |
| `src/media.js` | `File` ↔ IndexedDB → object URLs | Keys `spot:<id>` (photo), `exercise:<id>` (video). Swap for a server store later. |
| `src/store.js` | `(state, action, pack)` → state; `select()` → view model | Pure reducer + localStorage (per pack). No DOM. |
| `src/map/isoMap.js` | `(scene, view)` → SVG string | Pure. Draws room, `topdown.png`, object boxes, route, spots, "you". |
| `src/map/threeMap.js` | `(el, scene, view)` → cleanup | Used when `scene.model` exists. Loads `model.js` once and shares one WebGL renderer; spots, route, walk leg and labels come from `view`. |
| `src/map/index.js` | `mountMap(el, scene, view)` → cleanup | Picks the Three.js or SVG renderer. Three.js is loaded only when needed. |
| `src/screens/*.js` | `render(view, pack)` → HTML, `map(view)` → map view, `mount(root, view, ctx)` → cleanup | One file per screen. Buttons use `data-action`. |
| `src/main.js` | wires everything | Re-renders the current screen on every state change. |
| `tools/scene-editor.html` | point cloud → `spots.json` | Three.js neon viewer; same-camera "Enter workout mode" preview. |

### Session state machine

```
scan ─scanned─▶ build ─built─▶ ready ─enterSpace─▶ home ─start─▶ navigate ─arrive─▶ move ─complete─▶ navigate … ─▶ done ─home─▶ home
(room video)  (rendering)    (final look)          │                                │
                                                    └─ openUpload ─▶ upload ─▶ home   └─exit─▶ home (progress kept)
```

Persisted per pack: `{ setupDone, screen, history{date:true}, today{date, done{spotId:seconds}} }`.
A completion is keyed by `spotId` for today's session, so repeated taps count once. A new day resets `today`.
The move timer is independent of the video: a short clip loops while the move lasts `durationSec`.
