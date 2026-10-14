"""Run VGGT on a scene's images/ folder and save cameras, depth and points.

Works on CUDA, Apple MPS or CPU. Output: <scene>/vggt/predictions.npz and points_raw.ply.

Usage:
  python pipeline/run_vggt.py --scene ../data/office --weights ../data/weights/vggt_1b.pt
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "third_party" / "vggt"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vggt.models.vggt import VGGT  # noqa: E402
from vggt.utils.geometry import unproject_depth_map_to_point_map  # noqa: E402
from vggt.utils.pose_enc import pose_encoding_to_extri_intri  # noqa: E402

from plyio import write_ply  # noqa: E402

PATCH = 14


def use_chunked_attention(budget_bytes: float = 1.5 * 2**30) -> None:
    """MPS/CPU SDPA materializes the full (heads, N, N) score matrix, which explodes in VGGT's
    global attention over many frames. Split queries so each chunk's scores stay under budget."""
    import torch.nn.functional as F

    sdpa = F.scaled_dot_product_attention

    def chunked(q, k, v, *args, **kwargs):
        n_q, n_k = q.shape[-2], k.shape[-2]
        per_row = q[..., :1, :1].numel() * n_k * q.element_size()
        step = max(1, int(budget_bytes // per_row))
        if n_q <= step or kwargs.get("attn_mask") is not None or (args and args[0] is not None):
            return sdpa(q, k, v, *args, **kwargs)
        return torch.cat([sdpa(q[..., i:i + step, :], k, v, *args, **kwargs) for i in range(0, n_q, step)], dim=-2)

    F.scaled_dot_product_attention = chunked


def model_size(w: int, h: int, long_side: int) -> tuple[int, int]:
    """Keep aspect ratio, long side = long_side, both sides multiples of 14."""
    s = long_side / max(w, h)
    return (max(PATCH, round(w * s / PATCH) * PATCH), max(PATCH, round(h * s / PATCH) * PATCH))


def load_images(paths: list[Path], long_side: int) -> tuple[torch.Tensor, tuple[int, int]]:
    imgs = [Image.open(p).convert("RGB") for p in paths]
    # One shared size, from the majority orientation; the minority gets center-cropped.
    portrait = sum(i.height > i.width for i in imgs) * 2 >= len(imgs)
    ref = next(i for i in imgs if (i.height > i.width) == portrait)
    tw, th = model_size(ref.width, ref.height, long_side)
    out = []
    for im in imgs:
        s = max(tw / im.width, th / im.height)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.BICUBIC)
        l, t = (im.width - tw) // 2, (im.height - th) // 2
        im = im.crop((l, t, l + tw, t + th))
        out.append(torch.from_numpy(np.asarray(im)).permute(2, 0, 1).float() / 255.0)
    return torch.stack(out), (th, tw)


def pick_device(name: str) -> str:
    if name != "auto":
        return name
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--weights", default=str(ROOT / "data" / "weights" / "vggt_1b.pt"))
    ap.add_argument("--device", default="auto")
    ap.add_argument("--long-side", type=int, default=518, help="model input long side (518 = training size)")
    ap.add_argument("--max-images", type=int, default=40)
    ap.add_argument("--conf-percentile", type=float, default=40, help="drop this % lowest-confidence points in the preview ply")
    args = ap.parse_args()

    scene = Path(args.scene)
    paths = sorted((scene / "images").glob("*.jpg"))
    if len(paths) > args.max_images:
        idx = np.linspace(0, len(paths) - 1, args.max_images).round().astype(int)
        paths = [paths[i] for i in idx]
    out_dir = scene / "vggt"
    out_dir.mkdir(parents=True, exist_ok=True)

    device = pick_device(args.device)
    if device != "cuda":
        use_chunked_attention()
    images, hw = load_images(paths, args.long_side)
    print(f"{len(paths)} images at {hw[1]}x{hw[0]} on {device}")

    t0 = time.time()
    model = VGGT()
    model.load_state_dict(torch.load(args.weights, map_location="cpu"))
    model = model.to(device).eval()
    print(f"model loaded in {time.time() - t0:.1f}s")

    t0 = time.time()
    with torch.no_grad():
        if device == "cuda":
            dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
            with torch.autocast("cuda", dtype=dtype):
                pred = model(images.to(device))
        else:
            pred = model(images.to(device))
    if device == "mps":
        torch.mps.synchronize()
    infer_s = time.time() - t0
    print(f"inference {infer_s:.1f}s")

    extrinsic, intrinsic = pose_encoding_to_extri_intri(pred["pose_enc"], hw)
    to_np = lambda t: t.float().cpu().numpy().squeeze(0)  # noqa: E731
    extrinsic, intrinsic = to_np(extrinsic), to_np(intrinsic)
    depth, depth_conf = to_np(pred["depth"]), to_np(pred["depth_conf"])
    # Depth + camera unprojection is more accurate than the point head (VGGT README).
    points = unproject_depth_map_to_point_map(depth, extrinsic, intrinsic).astype(np.float32)
    rgb = (images.permute(0, 2, 3, 1).numpy() * 255).round().astype(np.uint8)

    np.savez_compressed(
        out_dir / "predictions.npz",
        extrinsic=extrinsic, intrinsic=intrinsic, depth=depth[..., 0], depth_conf=depth_conf,
        world_points=points, images=rgb,
        image_files=np.array([p.name for p in paths]),
    )

    conf = depth_conf.reshape(-1)
    keep = conf > np.percentile(conf, args.conf_percentile)
    write_ply(out_dir / "points_raw.ply", points.reshape(-1, 3)[keep], rgb.reshape(-1, 3)[keep])

    meta = {"device": device, "numImages": len(paths), "modelInputHW": list(hw),
            "inferenceSeconds": round(infer_s, 1), "weights": Path(args.weights).name,
            "torch": torch.__version__}
    if device == "mps":
        meta["mpsDriverAllocatedGB"] = round(torch.mps.driver_allocated_memory() / 2**30, 2)
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta))
    print(f"saved {out_dir}/predictions.npz, points_raw.ply ({keep.sum():,} pts)")


if __name__ == "__main__":
    main()
