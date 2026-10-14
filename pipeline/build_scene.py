"""Turn VGGT predictions into an app-space scene: meters, Y up, floor at y=0.

Steps: confidence filter -> RANSAC floor plane -> metric scale -> align walls to X/Z
-> crop + voxel downsample -> points.ply, scene.json (transform + cameras + topdown), topdown.png.

Output is the geometry half of a web content pack (web/content/<id>/). spots.json, exercises.json
and session.json are authored separately (web/tools/scene-editor.html, or copied from content/demo).

Scale: by default assumes the phone was held ~1.35 m above the floor (median over photos).
Override with --scale (multiplier on VGGT units) once a real measurement is known.

Usage:
  python pipeline/build_scene.py --scene ../data/office --out web/content/office
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plyio import write_ply  # noqa: E402

CV_TO_GL = np.diag([1.0, -1.0, -1.0])  # OpenCV camera axes -> Three.js camera axes


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), float(a @ b)
    if np.linalg.norm(v) < 1e-8:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def fit_floor(pts: np.ndarray, up_hint: np.ndarray, cam_centers: np.ndarray, thr: float,
              iters: int = 2000, max_tilt_deg: float = 30, seed: int = 0):
    """RANSAC plane among the lowest points, normal near up_hint. Returns (normal, point on plane)."""
    rng = np.random.default_rng(seed)
    h = pts @ up_hint
    low = pts[h < min(np.percentile(h, 30), (cam_centers @ up_hint).min())]
    if len(low) < 100:
        low = pts[h < np.percentile(h, 30)]
    if len(low) > 200_000:
        low = low[rng.choice(len(low), 200_000, replace=False)]
    cos_max = np.cos(np.radians(max_tilt_deg))
    best_n, best_p, best_cnt = None, None, -1
    for _ in range(iters):
        a, b, c = low[rng.choice(len(low), 3, replace=False)]
        n = np.cross(b - a, c - a)
        nn = np.linalg.norm(n)
        if nn < 1e-12:
            continue
        n /= nn
        if abs(n @ up_hint) < cos_max:
            continue
        cnt = int((np.abs((low - a) @ n) < thr).sum())
        if cnt > best_cnt:
            best_n, best_p, best_cnt = n, a, cnt
    if best_n is None:
        raise SystemExit("floor fit failed: no near-horizontal plane found")
    inl = low[np.abs((low - best_p) @ best_n) < thr]
    centroid = inl.mean(0)
    n = np.linalg.svd(inl - centroid, full_matrices=False)[2][-1]
    if n @ (cam_centers.mean(0) - centroid) < 0:
        n = -n
    return n, centroid, len(inl) / len(low)


def manhattan_yaw(xz: np.ndarray, cell: float) -> float:
    """Dominant wall/edge direction (radians, mod 90deg) from an occupancy image of the XZ footprint."""
    lo, hi = np.percentile(xz, 1, 0), np.percentile(xz, 99, 0)
    ij = ((xz - lo) / cell).astype(int)
    shape = (np.ceil((hi - lo) / cell).astype(int) + 1)
    ok = (ij >= 0).all(1) & (ij < shape).all(1)
    occ = np.zeros(shape[::-1], np.float32)
    np.add.at(occ, (ij[ok, 1], ij[ok, 0]), 1)
    occ = cv2.GaussianBlur(np.log1p(occ), (0, 0), 1.5)
    gx, gy = cv2.Sobel(occ, cv2.CV_32F, 1, 0), cv2.Sobel(occ, cv2.CV_32F, 0, 1)
    mag = np.hypot(gx, gy).ravel()
    ang = np.mod(np.arctan2(gy, gx).ravel(), np.pi / 2)
    m = mag > np.percentile(mag, 90)
    hist, edges = np.histogram(ang[m], bins=90, range=(0, np.pi / 2), weights=mag[m])
    hist = np.convolve(np.r_[hist[-2:], hist, hist[:2]], np.ones(5) / 5, "valid")
    k = int(hist.argmax())
    return float((edges[k] + edges[k + 1]) / 2)


def voxel_downsample(xyz: np.ndarray, rgb: np.ndarray, voxel: float, min_count: int):
    keys = np.floor(xyz / voxel).astype(np.int64)
    _, inv, cnt = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    sx = np.zeros((len(cnt), 3))
    sc = np.zeros((len(cnt), 3))
    np.add.at(sx, inv, xyz)
    np.add.at(sc, inv, rgb.astype(np.float64))
    keep = cnt >= min_count
    return (sx[keep] / cnt[keep, None]).astype(np.float32), (sc[keep] / cnt[keep, None]).round().astype(np.uint8)


def topdown_png(xyz: np.ndarray, path: Path, cell: float = 0.02, max_y: float = 2.2) -> dict:
    """Height-coloured top view. Returns where it sits on the floor, for the web map."""
    m = xyz[:, 1] < max_y
    p = xyz[m]
    lo, hi = p[:, [0, 2]].min(0), p[:, [0, 2]].max(0)
    w, h = (np.ceil((hi - lo) / cell).astype(int) + 1)
    ij = ((p[:, [0, 2]] - lo) / cell).astype(int)
    hmap = np.full((h, w), -1.0, np.float32)
    np.maximum.at(hmap, (ij[:, 1], ij[:, 0]), p[:, 1])
    img = np.zeros((h, w, 3), np.float32) + np.array([11, 15, 14])  # #0B0F0E
    floor = (hmap >= -0.5) & (hmap < 0.08)
    obj = hmap >= 0.08
    img[floor] = [22, 34, 31]
    t = np.clip(hmap[obj] / 1.5, 0, 1)[:, None]
    img[obj] = (1 - t) * np.array([30, 110, 100]) + t * np.array([94, 230, 208])  # -> #5EE6D0
    Image.fromarray(img.astype(np.uint8)).save(path)
    return {"file": path.name, "cellMeters": cell, "originXZ": lo.round(4).tolist(), "sizePx": [int(w), int(h)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, help="folder with vggt/predictions.npz")
    ap.add_argument("--out", required=True, help="web content pack folder, e.g. web/content/office")
    ap.add_argument("--scene-id", default=None)
    ap.add_argument("--conf-percentile", type=float, default=50)
    ap.add_argument("--camera-height", type=float, default=1.35, help="assumed median phone height (m)")
    ap.add_argument("--scale", type=float, default=None, help="explicit VGGT-units -> meters multiplier")
    ap.add_argument("--voxel", type=float, default=0.015)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--max-height", type=float, default=3.2)
    args = ap.parse_args()

    scene, out = Path(args.scene), Path(args.out)
    d = np.load(scene / "vggt" / "predictions.npz")
    E, K, P, conf, imgs = d["extrinsic"], d["intrinsic"], d["world_points"], d["depth_conf"], d["images"]
    files = [str(f) for f in d["image_files"]]
    S, H, W = conf.shape

    m = conf > max(np.percentile(conf, args.conf_percentile), 1.05)
    xyz, rgb = P[m].astype(np.float64), imgs[m]

    R, t = E[:, :, :3], E[:, :, 3]
    C = -np.einsum("sji,sj->si", R, t)  # camera centers in VGGT world
    up_hint = (-R[:, 1, :]).mean(0)  # OpenCV y points down
    up_hint /= np.linalg.norm(up_hint)

    extent = np.median(np.linalg.norm(xyz - C.mean(0), axis=1))
    n, p0, inlier_frac = fit_floor(xyz, up_hint, C, thr=0.01 * extent)
    tilt = np.degrees(np.arccos(np.clip(n @ up_hint, -1, 1)))

    R_up = rotation_between(n, np.array([0.0, 1.0, 0.0]))
    cam_h = (C - p0) @ n
    if args.scale is not None:
        s, scale_src = args.scale, "explicit"
    else:
        s, scale_src = args.camera_height / np.median(cam_h), f"assumed median camera height {args.camera_height} m"

    y_up = (s * (xyz - p0) @ R_up.T)
    band = (y_up[:, 1] > 0.3) & (y_up[:, 1] < 2.0)
    yaw = manhattan_yaw(y_up[band][:, [0, 2]], cell=0.03) if band.sum() > 1000 else 0.0
    c, sn = np.cos(yaw), np.sin(yaw)
    R_yaw = np.array([[c, 0, sn], [0, 1, 0], [-sn, 0, c]])
    R_app = R_yaw @ R_up

    app = s * (xyz - p0) @ R_app.T
    lo, hi = np.percentile(app[:, [0, 2]], 1, 0), np.percentile(app[:, [0, 2]], 99, 0)
    margin = 0.1 * (hi - lo)
    center = (lo + hi) / 2
    offset = np.array([-center[0], 0.0, -center[1]])
    app += offset

    M = np.eye(4)
    M[:3, :3] = s * R_app
    M[:3, 3] = offset - s * R_app @ p0

    keep = ((app[:, [0, 2]] >= lo - margin + offset[[0, 2]]).all(1)
            & (app[:, [0, 2]] <= hi + margin + offset[[0, 2]]).all(1)
            & (app[:, 1] > -0.15) & (app[:, 1] < args.max_height))
    pts, cols = voxel_downsample(app[keep], rgb[keep], args.voxel, args.min_count)

    out.mkdir(parents=True, exist_ok=True)
    (out / "photos").mkdir(exist_ok=True)
    write_ply(out / "points.ply", pts, cols)
    topdown = topdown_png(pts, out / "topdown.png")

    cams = []
    for i in range(S):
        pos = (M @ np.r_[C[i], 1.0])[:3]
        rot = R_app @ R[i].T @ CV_TO_GL
        fx, fy = K[i, 0, 0], K[i, 1, 1]
        src = scene / "images" / files[i]
        shutil.copy(src, out / "photos" / files[i])
        cams.append({
            "file": f"photos/{files[i]}",
            "position": pos.round(4).tolist(),
            "quaternion": Rotation.from_matrix(rot).as_quat().round(6).tolist(),  # x, y, z, w
            "fovY": round(float(np.degrees(2 * np.arctan(H / (2 * fy)))), 3),
            "aspect": round(W / H, 5),
            "heightAboveFloor": round(float(pos[1]), 3),
        })

    bmin, bmax = pts.min(0), pts.max(0)
    meta = {
        "sceneId": args.scene_id or out.name,
        "sceneVersion": "v1",
        "origin": "vggt",
        "source": "VGGT-1B, depth+camera unprojection",
        "units": "meters",
        "upAxis": "Y",
        "floorY": 0.0,
        "scaleSource": scale_src,
        "vggtToApp": M.round(6).tolist(),
        "bounds": {"min": bmin.round(3).tolist(), "max": bmax.round(3).tolist()},
        "wallHeight": round(float(min(bmax[1], 3.0)), 2),
        "topdown": topdown,
        "objects": [],
        "numPoints": int(len(pts)),
        "floorFit": {"inlierFraction": round(float(inlier_frac), 3), "tiltFromCameraUpDeg": round(float(tilt), 2)},
        "cameras": cams,
    }
    (out / "scene.json").write_text(json.dumps(meta, indent=2))

    size = bmax - bmin
    print(f"points {len(pts):,}  size {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} m (x,y,z)")
    print(f"floor inliers {inlier_frac:.0%}, floor normal vs camera-up {tilt:.1f} deg, yaw {np.degrees(yaw):.1f} deg")
    print(f"camera heights: {np.round([c['heightAboveFloor'] for c in cams], 2).tolist()}")
    print(f"scale: {scale_src}  (s={s:.4f})")
    print(f"-> {out}/points.ply, scene.json, topdown.png")


if __name__ == "__main__":
    main()
