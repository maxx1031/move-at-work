"""Runs the official Wan2.2-Animate preprocessing (third_party/Wan2.2/wan/modules/animate/preprocess/preprocess_data.py)
unchanged, with one runtime substitution:

SAM2 (pinned commit 0e78a11, as in requirements_animate.txt) always builds its CUDA extension `sam2._C`, used only
by `get_connected_components` for mask hole filling. In this env (torch 2.6 cu124 + system nvcc 12.1 + onnxruntime-gpu
in the same process) that kernel segfaults inside the preprocessing pipeline, although it works standalone.
We replace it with an equivalent CPU implementation (scipy.ndimage.label, 8-connectivity like the CUDA kernel);
the masks are 256x256 so the cost is negligible.

Usage: python preprocess.py <same args as preprocess_data.py>
"""
import os
import runpy
import sys

import numpy as np
import torch
from scipy import ndimage

import sam2.utils.misc as sam2_misc

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
PRE = os.path.join(ROOT, "third_party/Wan2.2/wan/modules/animate/preprocess")


def get_connected_components_cpu(mask: torch.Tensor):
    m = mask.detach().to("cpu", torch.uint8).numpy()
    labels = np.zeros(m.shape, dtype=np.int32)
    counts = np.zeros(m.shape, dtype=np.int32)
    struct = np.ones((3, 3), dtype=bool)
    for i in range(m.shape[0]):
        lab, n = ndimage.label(m[i, 0], structure=struct)
        if n:
            area = np.bincount(lab.ravel())
            area[0] = 0
            labels[i, 0] = lab
            counts[i, 0] = area[lab]
    return (torch.from_numpy(labels).to(mask.device), torch.from_numpy(counts).to(mask.device))


sam2_misc.get_connected_components = get_connected_components_cpu

if __name__ == "__main__":
    sys.path.insert(0, PRE)
    script = os.path.join(PRE, "preprocess_data.py")
    sys.argv = [script] + sys.argv[1:]
    runpy.run_path(script, run_name="__main__")
