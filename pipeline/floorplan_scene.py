"""Hand-authored office pack from the annotated floor-plan sketch (fallback when reconstruction is too noisy).

Positions come from the sketch (pixel coordinates below); object sizes are typical real-world sizes,
except the booth table, which was measured in the VGGT reconstruction (1.4 x 0.8 m, top at 0.72 m).
The sketch is not to scale: METERS_PER_PX makes the room ~13.7 x 10 m, matching the reconstruction extent.

The map is rotated 180 deg from the sketch so the street windows and entrance sit on a back wall,
which is the side the isometric map draws.

Usage:
  python pipeline/floorplan_scene.py --out web/content/office
"""
import argparse
import json
import math
import shutil
from pathlib import Path

METERS_PER_PX = 0.0145
ROOM_PX = (103, 57, 1048, 745)  # x0, y0, x1, y1 of the walls in the sketch
CX, CY = (ROOM_PX[0] + ROOM_PX[2]) / 2, (ROOM_PX[1] + ROOM_PX[3]) / 2
WALL_H = 3.0


def xz(px: float, py: float) -> list[float]:
    """Sketch pixel -> app XZ (meters, room centered, rotated 180 deg)."""
    return [round(-(px - CX) * METERS_PER_PX, 3), round(-(py - CY) * METERS_PER_PX, 3)]


def obj(id_, kind, px, py, size, y0=0.0, **extra):
    x, z = xz(px, py)
    return {"id": id_, "kind": kind, "position": [x, round(y0, 3), z], "size": [round(v, 3) for v in size], **extra}


def arc_pts(center, r, a0, a1, step_m):
    """Points on a sketch-space arc (angles in degrees, y down), spaced ~step_m apart."""
    n = max(2, round(math.radians(abs(a1 - a0)) * r * METERS_PER_PX / step_m) + 1)
    return [(center[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
             center[1] - r * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]


def build_objects():
    hx = (ROOM_PX[2] - ROOM_PX[0]) * METERS_PER_PX / 2
    hz = (ROOM_PX[3] - ROOM_PX[1]) * METERS_PER_PX / 2
    o = []

    # Street-side glass (sketch bottom wall -> app back wall z = -hz), split by the entrance.
    for i, (a, b) in enumerate([(103, 270), (410, 1048)], 1):
        (xa, _), (xb, _) = xz(a, 745), xz(b, 745)
        o.append({"id": f"window_{i:02d}", "kind": "window", "position": [round((xa + xb) / 2, 3), 0.1, round(-hz + 0.01, 3)],
                  "size": [round(abs(xa - xb), 3), 2.4, 0.02]})

    # Phone booths against the sketch's left wall (app x = +hx).
    for i, py in enumerate([456, 611], 1):
        b = obj(f"phone_booth_{i:02d}", "booth", 176, py, [1.2, 2.2, 1.3])
        b["position"][0] = round(hx - 0.65, 3)
        o.append(b)

    # Sofa booth + the wooden booth table in front of it (measured in the reconstruction).
    o.append(obj("sofa_booth_01", "sofa", 334, 303, [2.4, 1.1, 1.0]))
    o.append(obj("desk_booth_01", "desk", 334, 392, [1.4, 0.72, 0.8], note="measured: top 0.72 m, ~1.4 x 0.8 m"))

    # Raised platform (circle in the sketch) and its curved railing as posts.
    plat_c, plat_r = (580, 228), 152
    d = 2 * plat_r * METERS_PER_PX
    o.append(obj("platform_01", "platform", *plat_c, [d, 0.45, d], shape="cylinder"))
    for i, (px, py) in enumerate(arc_pts(plat_c, 172, 205, -40, 0.7), 1):
        o.append(obj(f"rail_{i:02d}", "rail", px, py, [0.08, 1.05, 0.08]))

    # High table + two stools (top right of the sketch).
    o.append(obj("high_table_01", "table", 880, 172, [0.8, 1.05, 0.8], shape="cylinder"))
    for i, (px, py) in enumerate([(880, 112), (942, 190)], 1):
        o.append(obj(f"stool_{i:02d}", "stool", px, py, [0.4, 0.75, 0.4], shape="cylinder"))

    # Tall round table + five stools.
    o.append(obj("round_table_01", "table", 437, 556, [1.0, 1.05, 1.0], shape="cylinder"))
    for i, (px, py) in enumerate([(370, 522), (503, 522), (370, 595), (503, 595), (437, 628)], 3):
        o.append(obj(f"stool_{i:02d}", "stool", px, py, [0.4, 0.75, 0.4], shape="cylinder"))

    # Folding chairs, 3 x 3.
    n = 0
    for py in (475, 558, 641):
        for px in (656, 728, 800):
            n += 1
            o.append(obj(f"chair_{n:02d}", "chair", px, py, [0.45, 0.8, 0.45]))

    # Curved sofa along the sketch's right wall, as segments.
    for i, (px, py) in enumerate([(975, 385), (984, 470), (976, 555), (948, 635), (900, 700)], 1):
        o.append(obj(f"curved_sofa_{i:02d}", "sofa", px, py, [0.9, 0.8, 0.9]))
    return o, hx, hz


SPOTS = [  # route order: entrance -> booth table -> folding chairs -> railing
    {"id": "spot_01", "label": "Desk side", "shortName": "desk", "objectId": "desk_booth_01", "px": (334, 455),
     "clearanceRadius": 0.7, "exerciseIds": ["desk_stretch", "neck_shoulders"],
     "tip": "Stand facing the wooden booth table. Check it doesn't roll before you lean on it."},
    {"id": "spot_02", "label": "By the chair", "shortName": "chair", "objectId": "chair_04", "px": (606, 558),
     "clearanceRadius": 0.7, "exerciseIds": ["seated_calf_raise", "seated_knee_lift"],
     "tip": "Take an end folding chair by the aisle and keep your feet flat."},
    {"id": "spot_03", "label": "By the railing", "shortName": "railing", "objectId": "rail_15", "px": (770, 335),
     "clearanceRadius": 1.0, "exerciseIds": ["march_in_place", "standing_stretch"],
     "tip": "Stand on the open floor beside the platform railing, an arm's length of space around you."},
]

SESSION_STOPS = [
    {"spotId": "spot_01", "exerciseId": "desk_stretch", "title": "Desk stretch", "durationSec": 120},
    {"spotId": "spot_02", "exerciseId": "seated_calf_raise", "title": "Chair warm-up", "durationSec": 60},
    {"spotId": "spot_03", "exerciseId": "march_in_place", "title": "Floor moves", "durationSec": 120},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/content/office")
    ap.add_argument("--exercises-from", default="web/content/demo", help="pack to copy exercises.json + media from")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    objects, hx, hz = build_objects()
    ids = {o["id"] for o in objects}
    scene = {
        "sceneId": "office", "sceneVersion": "v1", "origin": "hand-authored",
        "note": "Laid out from the annotated floor-plan sketch; positions approximate, not measured. "
                "Booth table size from the VGGT reconstruction. Map rotated 180 deg vs the sketch (street windows at back).",
        "units": "meters", "upAxis": "Y", "floorY": 0.0, "wallHeight": WALL_H,
        "bounds": {"min": [round(-hx, 3), 0.0, round(-hz, 3)], "max": [round(hx, 3), WALL_H, round(hz, 3)]},
        "entry": [xz(340, 745)[0], 0.0, round(-hz + 0.3, 3)],
        "objects": objects,
    }
    spots = []
    for s in SPOTS:
        assert s["objectId"] in ids, s["objectId"]
        x, z = xz(*s["px"])
        spots.append({k: v for k, v in s.items() if k != "px"} | {"position": [x, 0.0, z], "headingRadians": 0.0,
                                                                   "reviewed": False})
    (out / "scene.json").write_text(json.dumps(scene, indent=2))
    (out / "spots.json").write_text(json.dumps({"sceneId": "office", "sceneVersion": "v1", "units": "meters",
                                                "upAxis": "Y", "spots": spots}, indent=2))
    (out / "session.json").write_text(json.dumps({"id": "daily_5min", "sceneId": "office", "reminder": "15:00",
                                                  "stops": SESSION_STOPS}, indent=2))
    src = Path(args.exercises_from)
    shutil.copy(src / "exercises.json", out / "exercises.json")
    if (src / "media").exists():
        shutil.copytree(src / "media", out / "media", dirs_exist_ok=True)
    print(f"{len(objects)} objects, {len(spots)} spots -> {out}  "
          f"room {2 * hx:.1f} x {2 * hz:.1f} m")


if __name__ == "__main__":
    main()
