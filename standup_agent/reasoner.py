"""Scene reasoning with a local VLM (Qwen3-VL): what objects are in the video, what they afford, which 3
exercises to suggest, and which frame shows each object best.

Exercises are chosen only from standup_agent/catalog.json (names/categories follow the ExRx.net directory).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import base64
import io
import os

import cv2
import numpy as np
from PIL import Image

from . import tracing as T

REPO = Path(__file__).resolve().parents[1]
DEFAULT_VLM = REPO / "checkpoints/qwen3vl/Qwen3-VL-8B-Instruct"
CATALOG = Path(__file__).with_name("catalog.json")


def load_catalog():
    return json.loads(CATALOG.read_text())


# ---------------------------------------------------------------- video frames

def read_frames(video, n=12, max_side=None):
    """Uniformly sample n frames; returns [(frame_index, PIL image)], plus fps and total frame count."""
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idxs = np.linspace(0, total - 1, min(n, total)).round().astype(int)
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if not ok:
            continue
        im = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        if max_side:
            im.thumbnail((max_side, max_side))
        out.append((int(i), im))
    cap.release()
    return out, fps, total


def read_frame(video, index):
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"cannot read frame {index} of {video}")
    return Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))


def sharpness(im: Image.Image) -> float:
    g = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


# ---------------------------------------------------------------- VLM

class VLM:
    """Local Qwen3-VL (needs a GPU). See BedrockVLM for the hosted alternative with the same interface."""

    def __init__(self, path=DEFAULT_VLM, device="cuda"):
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            str(path), dtype=torch.bfloat16, device_map=device).eval()
        self.processor = AutoProcessor.from_pretrained(str(path))
        self.name = Path(path).name

    def ask(self, images, text, max_new_tokens=1500):
        import torch
        content = []
        for label, im in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "image": im})
        content.append({"type": "text", "text": text})
        msgs = [{"role": "user", "content": content}]
        with T.llm_span(self.name, images, text, {"max_new_tokens": max_new_tokens, "do_sample": False}) as s, \
                torch.no_grad():
            inputs = self.processor.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                                        return_dict=True, return_tensors="pt").to(self.model.device)
            out = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            n_in = inputs["input_ids"].shape[1]
            raw = self.processor.batch_decode(out[:, n_in:], skip_special_tokens=True)[0]
            T.set_llm_output(s, raw, n_in, out.shape[1] - n_in)
        return raw

    def ask_json(self, images, text, retries=1, **kw):
        for attempt in range(retries + 1):
            raw = self.ask(images, text if attempt == 0 else text + "\nReturn ONLY valid JSON.", **kw)
            obj = parse_json(raw)
            if obj is not None:
                return obj, raw
            T.event("vlm.invalid_json", attempt=attempt)
        raise ValueError(f"VLM did not return JSON:\n{raw}")


DEFAULT_BEDROCK_MODEL = "global.anthropic.claude-opus-5-5"
FALLBACK_BEDROCK_MODEL = "global.anthropic.claude-opus-4-8"


class BedrockVLM(VLM):
    """Claude on Amazon Bedrock (bedrock-runtime endpoint), same ask()/ask_json() interface as the local VLM.

    Auth: BEDROCK_API_KEY (a short-term Bedrock bearer token; inside a Daytona sandbox this is a secret placeholder
    the egress proxy swaps for the real token) or else the standard AWS credential chain. Region: AWS_REGION."""

    def __init__(self, model=DEFAULT_BEDROCK_MODEL, effort=None):
        from anthropic import AnthropicBedrock, BetaRefusalFallbackMiddleware
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
        # If Claude declines a request, the middleware retries it once on the fallback model.
        self.client = AnthropicBedrock(aws_region=region, api_key=os.environ.get("BEDROCK_API_KEY") or None,
                                       middleware=[BetaRefusalFallbackMiddleware([{"model": FALLBACK_BEDROCK_MODEL}])])
        self.name = model
        self.effort = effort or os.environ.get("STANDUP_VLM_EFFORT", "medium")

    def ask(self, images, text, max_new_tokens=1500):
        # max_new_tokens sized the local model's answer; Claude also spends output tokens on thinking, so give it room.
        content = []
        for label, im in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": _jpeg_b64(im)}})
        content.append({"type": "text", "text": text})
        # Thinking is always on; display="summarized" returns a readable summary of it (the default, "omitted",
        # returns empty thinking blocks). It costs nothing extra and is recorded on the trace as the reasoning.
        thinking = {"type": "adaptive", "display": "summarized"}
        params = {"max_tokens": 16000, "effort": self.effort, "thinking": thinking}
        with T.llm_span(self.name, images, text, params, provider="aws", system="anthropic") as s:
            r = self.client.beta.messages.create(model=self.name, max_tokens=16000, thinking=thinking,
                                                 output_config={"effort": self.effort},
                                                 messages=[{"role": "user", "content": content}])
            raw = "".join(b.text for b in r.content if b.type == "text")
            reasoning = "\n\n".join(b.thinking for b in r.content if b.type == "thinking" and b.thinking)
            if r.stop_reason == "refusal":
                T.event("vlm.refusal", category=str(getattr(r.stop_details, "category", None)))
                raw = ""
            s.set_attribute("llm.model_name", r.model)
            T.set_llm_output(s, raw, r.usage.input_tokens, r.usage.output_tokens, reasoning=reasoning)
        return raw


def make_vlm(spec, device="cuda"):
    """'bedrock' or 'bedrock:<model id>' for Claude on Bedrock; anything else is a local Qwen3-VL checkpoint path."""
    if spec == "bedrock" or spec.startswith("bedrock:"):
        return BedrockVLM(spec.split(":", 1)[1] if ":" in spec else DEFAULT_BEDROCK_MODEL)
    return VLM(spec, device=device)


def _jpeg_b64(im, quality=85):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def parse_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw)
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            try:
                return json.loads(raw[start:i + 1])
            except json.JSONDecodeError:
                return None
    return None


# ---------------------------------------------------------------- reasoning steps

IDENTITY_Q = """These photos all show the same person (the app user). Describe their visible appearance for a video
generation prompt in ONE short noun phrase: apparent gender presentation, approximate age group, hair, and clothing
(colors and items), e.g. "a young woman with a black cap, brown T-shirt and black leggings".
Return JSON: {"appearance": "...", "pronoun": "she|he|they"}"""

SCENE_Q = """These frames come from one phone video of a workplace (labels give each frame's index in the video).
A user wants to do short workouts here using what is in the scene. List the distinct physical objects or areas that
could support a bodyweight exercise. Use only these affordance types:
{affordances}
List EVERY object that could support at least one of these, even if it needs a caveat: put caveats in "concerns"
rather than leaving the object out (e.g. a folding chair is a "seat" with the concern that it may fold or tip).
Hard safety rules: anything on wheels/casters is never a "seat" or "step"; chairs (folding or not) are never a "step";
glass tables are never a "table_edge"; glass walls, windows and glass doors are never a "wall"; only list "floor_open"
if there is clearly enough clear floor.
Also classify the setting as one of: "moving_vehicle" (inside a train, bus, car...), "transit_platform" (train/subway
platform or station area near tracks), "indoor", "outdoor".
Return JSON:
{{"scene": "one sentence describing ONLY the static place (architecture, surfaces, furniture, lighting); do NOT
            mention any people, animals or vehicles, even if visible",
  "setting": "moving_vehicle|transit_platform|indoor|outdoor",
  "objects": [{{"name": "short name, e.g. 'white folding chair'",
               "description": "color/material/size/position",
               "affordances": ["seat", ...],
               "concerns": "safety caveats or empty string",
               "frames": [frame indices (from the labels) where it is clearly and fully visible]}}]}}"""

PLAN_Q = """Scene: {scene}
Objects available (with what they safely afford):
{objects}

Candidate exercises (from the ExRx.net exercise directory; you must choose ids from this list only):
{candidates}

Pick exactly 3 exercises for this user. Rules: each exercise must use an object whose affordances include the
exercise's affordance (or "floor_open" for open floor); use 3 different objects/areas when possible; the 3 must be
from 3 DIFFERENT categories (the [brackets]) so they train different body regions;
respect the safety concerns; never place the user near a platform edge, tracks, traffic or stairs' top edge;
prefer exercises that USE the objects in the scene (at most ONE open-floor exercise); strongly prefer exercises with
visibility "high" (clearly visible movement in a 5 s video).
For each, choose the frame index (from that object's frames) that best shows the object AND has room for a person
next to it.
Return JSON:
{{"suggestions": [{{"exercise_id": "...", "object": "object name exactly as listed", "frame": frame_index,
                   "side": "left|right (which leg/arm leads, if relevant)",
                   "why": "one sentence: why this exercise suits this object/scene",
                   "safety": "one sentence safety cue for the user"}}]}}"""


FRAME_Q = """The user will do "{exercise}" using the {object} ({description}). The exercise needs: {start_pose}.
Which ONE of these frames (labels give frame indices) is the best background for a video of this: the {object} is
fully visible and in focus, there is free space next to it for a person in that pose, and the person's whole body
would fit in the frame? Strongly prefer frames with NO people visible (the generated person must be the only one).
Return JSON: {{"frame": frame_index, "people_visible": true|false, "reason": "..."}}"""


@dataclass
class Suggestion:
    exercise: dict
    object: dict
    frame_index: int
    why: str
    safety: str
    side: str
    people_visible: bool = False
    object_verified: bool = False


def describe_identity(vlm, photos):
    ims = [(f"Photo {i + 1}:", _thumb(Image.open(p).convert("RGB"))) for i, p in enumerate(photos)]
    with T.span("describe_identity", input={"photos": [str(p) for p in photos]}) as s:
        obj, _ = vlm.ask_json(ims, IDENTITY_Q, max_new_tokens=200)
        T.set_output(s, obj)
    return obj


# Hard safety rules enforced in code (the VLM does not always follow them): (rule, words in the object's name or
# description, affordances such an object may not have).
OBJECT_RULES = [
    ("glass_is_not_support", ("glass", "window"), ("wall", "table_edge")),
    ("wheels_are_not_seat_or_step", ("wheel", "caster", "castor", "rolling", "office chair", "swivel"), ("seat", "step")),
    ("chair_is_not_step", ("chair", "bench"), ("step",)),
    ("uneven_is_not_step", ("rock", "boulder", "stone", "log", "grass", "hill", "slope"), ("step",)),
    ("post_is_not_rail_or_step", ("bollard", "post", "pole", "tree", "trunk", "pillar", "column", "sign",
                                  "turnstile", "gate", "barrier", "bike rack", "bicycle"), ("rail", "step")),
]


# Exercise-level exclusions: (rule, words in the object's name/description, exercises not allowed on it).
EXERCISE_RULES = [
    # folding chairs must not take load on the front edge (dips / push-ups tip them)
    ("folding_chair_no_edge_load", ("folding",), ("bench_dip", "incline_pushup_seat")),
    # stair handrails are inclined: fine to grip, not a place to rest a heel
    ("stair_rail_not_for_heel", ("stair", "staircase", "escalator"), ("hamstring_stretch_rail",)),
]


def analyze_scene(vlm, frames, catalog):
    ims = [(f"Frame index {i}:", _thumb(im)) for i, im in frames]
    aff = "\n".join(f"- {k}: {v}" for k, v in catalog["affordances"].items())
    with T.span("analyze_scene", input={"frames": [i for i, _ in frames]}) as s:
        obj, raw = vlm.ask_json(ims, SCENE_Q.format(affordances=aff))
        with T.span("safety_guardrails", "GUARDRAIL", input=obj) as g:
            overrides = apply_safety_rules(obj, catalog, {i for i, _ in frames})
            T.set_output(g, {"overrides": overrides, "objects": obj["objects"]},
                         **{"guardrail.overrides": len(overrides),
                            "guardrail.objects_dropped": sum(o["rule"] == "no_safe_affordance" for o in overrides)})
        obj["guardrail_overrides"] = overrides
        T.set_output(s, obj)
    return obj


def apply_safety_rules(obj, catalog, frame_ids):
    """Remove affordances the safety rules forbid, in place; return what was overridden (also traced as events)."""
    overrides = []

    def remove(o, banned, rule):
        gone = [a for a in o["affordances"] if a in banned]
        if gone:
            o["affordances"] = [a for a in o["affordances"] if a not in banned]
            overrides.append(dict(rule=rule, object=o.get("name", ""), removed=gone))
            T.event("guardrail.override", rule=rule, object=o.get("name", ""), removed=gone)

    valid = set(catalog["affordances"])
    for o in obj.get("objects", []):
        o["affordances"] = list(o.get("affordances", []))
        remove(o, set(o["affordances"]) - valid, "unknown_affordance")
        o["frames"] = [int(f) for f in o.get("frames", []) if int(f) in frame_ids] or sorted(frame_ids)
    for o in obj.get("objects", []):
        text = f"{o.get('name', '')} {o.get('description', '')}".lower()
        for rule, words, banned in OBJECT_RULES:
            if any(w in text for w in words):
                remove(o, banned, rule)
    # Exercise-level exclusions: folding chairs must not take load on the front edge (dips / push-ups tip them).
    for o in obj.get("objects", []):
        text = f"{o.get('name', '')} {o.get('description', '')}".lower()
        for rule, words, banned in EXERCISE_RULES:
            if any(w in text for w in words):
                o["no_exercises"] = sorted(set(o.get("no_exercises", [])) | set(banned))
                overrides.append(dict(rule=rule, object=o.get("name", ""), removed=list(banned)))
                T.event("guardrail.override", rule=rule, object=o.get("name", ""), removed=list(banned))
    # A table is never a seat (benches are); the scene text must not mention people or vehicles: it goes into the
    # video prompt, where any mention makes the model add them.
    for o in obj.get("objects", []):
        text = f"{o.get('name', '')}".lower()
        if "table" in text and "bench" not in text:
            o["affordances"] = [a for a in o["affordances"] if a != "seat"]
    obj["scene"] = scrub_scene(obj.get("scene", ""))
    # Setting-level safety: in a moving vehicle only seated exercises; on a platform nothing on the open floor
    # (it borders the platform edge / tracks).
    setting = str(obj.get("setting", "")).lower()
    for o in obj.get("objects", []):
        if setting == "moving_vehicle":
            remove(o, set(o["affordances"]) - {"seat"}, "moving_vehicle_seated_only")
        elif setting == "transit_platform":
            remove(o, {"floor_open"}, "platform_no_open_floor")
    for o in obj.get("objects", []):
        if not o["affordances"]:
            overrides.append(dict(rule="no_safe_affordance", object=o.get("name", ""), removed=[]))
            T.event("guardrail.object_dropped", object=o.get("name", ""))
    obj["objects"] = [o for o in obj.get("objects", []) if o["affordances"]]
    return overrides


_TRANSIENT = ("person", "people", "man", "woman", "men", "women", "child", "pedestrian", "passenger", "crowd",
              "commuter", "someone", "train", "car ", "cars", "bus", "bicycle", "bike", "vehicle", "dog", "bird",
              "pigeon")


def scrub_scene(text):
    """Keep only sentences / clauses about the static place."""
    keep = []
    for sent in re.split(r"(?<=[.;])\s+", text):
        clauses = [c for c in re.split(r",\s*(?:and\s+)?|\s+with\s+", sent)
                   if not any(w in f" {c.lower()} " for w in _TRANSIENT)]
        if clauses:
            out = ", ".join(c.strip().rstrip(".") for c in clauses)
            keep.append(out[0].upper() + out[1:] + ".")
    return " ".join(keep) or "An empty space."


def plan(vlm, frames, scene, catalog, n=3):
    present = {a for o in scene["objects"] for a in o["affordances"]}
    cands = [e for e in catalog["exercises"] if e["affordance"] in present]
    if str(scene.get("setting", "")).lower() == "moving_vehicle":  # stay seated in a moving vehicle
        cands = [e for e in cands if e["id"].startswith("seated_")]  # you stay in the seat the whole time
        n = min(n, len(cands))
    if not cands:
        raise ValueError("no catalog exercise matches the objects found in this scene")
    objects_txt = "\n".join(f"- {o['name']}: affords {o['affordances']}; frames {o['frames']}; concerns: {o.get('concerns') or 'none'}"
                            for o in scene["objects"])
    cands_txt = "\n".join(f"- {e['id']}: {e['name']} [{e['exrx_category']}] needs {e['affordance']}; level {e['level']}; targets {e['target']}; visibility in video {e.get('visibility', 'high')}"
                          for e in cands)
    ims = [(f"Frame index {i}:", _thumb(im)) for i, im in frames]
    with T.span("plan_exercises", input={"candidates": [e["id"] for e in cands], "n": n}) as s:
        obj, raw = vlm.ask_json(ims, PLAN_Q.format(scene=scene["scene"], objects=objects_txt, candidates=cands_txt))
        with T.span("plan_validation", "GUARDRAIL", input=obj) as g:
            out, rejected = _validate_plan(obj, scene, cands, n)
            T.set_output(g, {"accepted": [x.exercise["id"] for x in out], "rejected": rejected},
                         **{"guardrail.overrides": len(rejected),
                            "guardrail.fallback_picks": sum(x.why.startswith("fallback") for x in out)})
        T.set_output(s, [dict(exercise=x.exercise["id"], object=x.object["name"], frame=x.frame_index, why=x.why)
                         for x in out])
    return out


def _validate_plan(obj, scene, cands, n):
    """Keep the VLM's valid picks, fill up deterministically; returns (suggestions, rejected picks with reasons)."""
    by_id = {e["id"]: e for e in cands}
    by_name = {o["name"].lower(): o for o in scene["objects"]}
    out, used, cats, rejected = [], set(), set(), []

    def reject(s, rule):
        rejected.append(dict(rule=rule, exercise_id=s.get("exercise_id"), object=s.get("object")))
        T.event("guardrail.override", rule=rule, exercise_id=str(s.get("exercise_id")), object=str(s.get("object")))

    for s in obj.get("suggestions", []):
        ex = by_id.get(s.get("exercise_id"))
        o = by_name.get(str(s.get("object", "")).lower())
        # reject hallucinated ids / mismatched objects
        if ex is None:
            reject(s, "unknown_or_unsafe_exercise")
            continue
        if o is None:
            reject(s, "unknown_object")
            continue
        if ex["id"] in used:
            reject(s, "duplicate_exercise")
            continue
        if ex["affordance"] not in o["affordances"]:
            reject(s, "object_lacks_affordance")
            continue
        if ex["id"] in o.get("no_exercises", []):
            reject(s, "unsafe_for_object")
            continue
        if ex["exrx_category"] in cats:
            reject(s, "duplicate_category")
            continue  # enforce variety: one exercise per ExRx category (body region)
        used.add(ex["id"])
        cats.add(ex["exrx_category"])
        out.append(Suggestion(ex, o, int(s.get("frame", o["frames"][0])), s.get("why", ""), s.get("safety", ex["safety"]),
                              s.get("side", "left")))
    # The product is about using the scene: keep at most one open-floor exercise when object-based ones exist.
    floor = [x for x in out if x.exercise["affordance"] == "floor_open"]
    if len(floor) > 1 and any(e["affordance"] != "floor_open" for e in cands):
        drop = floor[1:]
        out = [x for x in out if x not in drop]
        used -= {x.exercise["id"] for x in drop}
        cats -= {x.exercise["exrx_category"] for x in drop}
        cands = sorted(cands, key=lambda e: e["affordance"] == "floor_open")  # object-based first for the fill-up
    cands = sorted(cands, key=lambda e: e.get("visibility") == "low")  # clearly visible exercises first (stable)
    # Prefer clearly visible exercises (a 5 s clip must read instantly; video models also fail at tiny motions):
    # swap a low-visibility pick for a high-visibility one on the same object when one is available.
    for k, x in enumerate(out):
        if x.exercise.get("visibility") != "low":
            continue
        alt = next((e for e in cands if e.get("visibility") != "low" and e["affordance"] in x.object["affordances"]
                    and e["id"] not in used and e["exrx_category"] not in cats
                    and e["id"] not in x.object.get("no_exercises", [])), None)
        if alt:
            used.discard(x.exercise["id"])
            cats.discard(x.exercise["exrx_category"])
            used.add(alt["id"])
            cats.add(alt["exrx_category"])
            T.event("plan.visibility_swap", object=x.object["name"], dropped=x.exercise["id"], added=alt["id"])
            out[k] = Suggestion(alt, x.object, x.frame_index, f"swapped in for visibility ({x.exercise['id']})",
                                alt["safety"], x.side)
    # Fill up deterministically if the VLM returned fewer valid picks.
    for e in cands:
        if len(out) >= n:
            break
        if e["id"] in used or e["exrx_category"] in cats:
            continue
        # prefer an object not used yet
        objs = [o for o in scene["objects"] if e["affordance"] in o["affordances"]
                and e["id"] not in o.get("no_exercises", [])]
        if not objs:
            continue
        o = next((o for o in objs if o["name"] not in {x.object["name"] for x in out}), objs[0])
        used.add(e["id"])
        cats.add(e["exrx_category"])
        out.append(Suggestion(e, o, o["frames"][0], "fallback pick (VLM gave fewer valid suggestions)", e["safety"], "left"))
    return out[:n], rejected


def choose_frame(vlm, frames, sugg: Suggestion):
    """Ask the VLM which sampled frame is the best background for this exercise; updates sugg.frame_index."""
    cand = [(i, im) for i, im in frames if i in set(sugg.object["frames"])] or frames
    ims = [(f"Frame index {i}:", _thumb(im)) for i, im in cand]
    q = FRAME_Q.format(exercise=sugg.exercise["name"], object=sugg.object["name"],
                       description=sugg.object.get("description", ""),
                       start_pose=sugg.exercise["start_pose"].format(object=sugg.object["name"]))
    with T.span("choose_frame", input={"exercise": sugg.exercise["id"], "object": sugg.object["name"],
                                        "frames": [i for i, _ in cand]}) as s:
        try:
            obj, _ = vlm.ask_json(ims, q, max_new_tokens=200)
            f = int(obj.get("frame", sugg.frame_index))
            if f in {i for i, _ in cand}:
                sugg.frame_index = f
            sugg.people_visible = bool(obj.get("people_visible", False))
        except (ValueError, TypeError):
            pass
        T.set_output(s, {"frame": sugg.frame_index, "people_visible": sugg.people_visible})
    return sugg


class PersonDetector:
    """COCO Faster R-CNN; counts people big enough to matter (a background person would be duplicated in generation)."""

    def __init__(self, device="cuda", min_height_frac=0.08, score=0.6):
        import torch
        from torchvision.models.detection import FasterRCNN_ResNet50_FPN_V2_Weights, fasterrcnn_resnet50_fpn_v2
        w = FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
        self.model = fasterrcnn_resnet50_fpn_v2(weights=w).to(device).eval()
        self.tf = w.transforms()
        self.device, self.min_h, self.score = device, min_height_frac, score
        self.no_grad = torch.no_grad

    def count(self, im: Image.Image) -> int:
        x = self.tf(im).to(self.device)
        with self.no_grad():
            o = self.model([x])[0]
        keep = (o["labels"] == 1) & (o["scores"] > self.score)
        h = (o["boxes"][keep, 3] - o["boxes"][keep, 1]) / im.height
        return int((h > self.min_h).sum())


_DETECTOR = None


def person_detector(device="cuda"):
    """The shared detector, or None when there is no GPU (e.g. in a CPU sandbox) or STANDUP_PERSON_DETECTOR=0:
    then frames are not screened for people and the VLM's people_visible answer is kept."""
    global _DETECTOR
    if _DETECTOR is None:
        try:
            import torch
            if os.environ.get("STANDUP_PERSON_DETECTOR") == "0" or not torch.cuda.is_available():
                return None
            _DETECTOR = PersonDetector(device)
        except ImportError:
            return None
    return _DETECTOR


def best_frame(video, sugg: Suggestion, all_indices, window=6, vlm=None, verify_top=8):
    """Around the VLM's chosen frame (and the object's listed frames), pick the sharpest full-resolution frame."""
    with T.span("best_frame", input={"exercise": sugg.exercise["id"], "object": sugg.object["name"],
                                     "vlm_frame": sugg.frame_index}) as s:
        i, im = _best_frame(video, sugg, all_indices, window, vlm, verify_top)
        T.set_output(s, {"frame": i, "people_visible": sugg.people_visible, "object_verified": sugg.object_verified})
    return i, im


def _best_frame(video, sugg, all_indices, window, vlm, verify_top):
    cands = set(sugg.object["frames"]) | {sugg.frame_index}
    step = max(1, (max(all_indices) - min(all_indices)) // max(len(all_indices) - 1, 1))
    near = set()
    for c in cands:
        near |= {c + d for d in range(-window, window + 1, max(1, step // 4))}
    near = sorted(i for i in near if min(all_indices) <= i <= max(all_indices))
    det = person_detector()
    scored = []
    for i in near:
        im = read_frame(video, i)
        people = det.count(im) if det else 0
        # Prefer people-free frames, then frames close to the VLM's choice, then sharpness.
        sc = sharpness(im) / (1 + abs(i - sugg.frame_index) / (4 * step)) * (0.05 if people else 1.0)
        scored.append((sc, i, im, people))
    scored.sort(key=lambda t: -t[0])
    ranked = [(i, im, people) for _, i, im, people in scored]
    sugg.object_verified = vlm is None
    if vlm is not None:
        # The object must be visible. Try people-free candidates first, then ones with people (flagged in the plan):
        # a frame showing the object with a bystander beats an empty frame without the object.
        # Check spread-out candidates (not near-duplicate neighbours) to cover the whole pan.
        tried = []
        for i, im, people in sorted(ranked, key=lambda t: t[2] > 0):
            if any(abs(i - j) < step for j in tried):
                continue
            tried.append(i)
            if object_visible(vlm, im, sugg.object, sugg.exercise["name"]):
                sugg.object_verified = True
                if det:
                    sugg.people_visible = people > 0
                return i, im
            if len(tried) >= verify_top:
                break
    i, im, people = ranked[0]
    if det:
        sugg.people_visible = people > 0
    return i, im


VISIBLE_Q = """Is the {name} ({description}) clearly visible in this image, large enough and unobstructed so that a person
could be shown using it for "{exercise}"? Return JSON: {{"visible": true|false}}"""


def object_visible(vlm, im, obj, exercise=""):
    with T.span("verify_object_visible", input={"object": obj["name"], "exercise": exercise}) as s:
        try:
            r, _ = vlm.ask_json([("Image:", _thumb(im))], VISIBLE_Q.format(name=obj["name"], description=obj.get("description", ""),
                                                                        exercise=exercise), max_new_tokens=60)
            visible = bool(r.get("visible", False))
        except ValueError:
            visible = False
        T.set_output(s, {"visible": visible})
    return visible


def _thumb(im, side=640):
    im = im.copy()
    im.thumbnail((side, side))
    return im
