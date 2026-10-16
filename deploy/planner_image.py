"""The per-upload planner sandbox image (Daytona declarative image; built once, then cached by Daytona for 24 h).

Python + the Claude / Arize / OpenCV deps; the entrypoint (planner_boot.py) fetches the planner code and one job
from the web server over the link network. No planner code is baked in, so code changes need no image rebuild.
"""
from pathlib import Path

PLANNER_DEPS = ["anthropic[bedrock]>=1.15", "arize-otel>=0.14", "opencv-python-headless>=4.10", "numpy", "pillow"]
BOOT = Path(__file__).with_name("planner_boot.py")


def planner_image():
    from daytona import Image
    return (Image.debian_slim("3.12").pip_install(PLANNER_DEPS)
            .add_local_file(str(BOOT), "/opt/standup/planner_boot.py")
            .entrypoint(["python", "/opt/standup/planner_boot.py"]))
