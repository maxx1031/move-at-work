"""Arize AX tracing for the workout agent (OpenTelemetry spans with OpenInference attributes).

Tracing is on when ARIZE_SPACE_ID and ARIZE_API_KEY are set (project: ARIZE_PROJECT_NAME, default "just-standup");
otherwise every span is a no-op. STANDUP_TRACE_CONSOLE=1 also prints spans; STANDUP_TRACE_IMAGES=0 leaves the
VLM's input images out of the traces.

One trace per scene video: plan_video (VLM calls, safety guardrails) and, from the parent process, the video
generation jobs, joined through the trace context stored in plan.json. All traces of one run share session.id.
"""
import base64
import io
import json
import os
from contextlib import contextmanager

try:
    from opentelemetry import context as otel_context, trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
except ImportError:  # envs without OpenTelemetry (e.g. a backend env importing reasoner): tracing is off
    trace = None

PROJECT = os.environ.get("ARIZE_PROJECT_NAME", "just-standup")
SESSION_ENV = "STANDUP_SESSION_ID"
_tracer = None


def setup():
    """Register the Arize exporter once per process (if credentials are set) and return the tracer."""
    global _tracer
    if _tracer is None and trace is not None:
        if os.environ.get("ARIZE_SPACE_ID") and os.environ.get("ARIZE_API_KEY"):
            if os.environ.get("STANDUP_ARIZE_TRANSPORT") == "http":
                _register_http()
            else:
                from arize.otel import register
                register(project_name=PROJECT, verbose=False,
                         log_to_console=os.environ.get("STANDUP_TRACE_CONSOLE") == "1")
        _tracer = trace.get_tracer("standup_agent")
    return _tracer


def _register_http():
    """OTLP over HTTPS to Arize (used in Daytona sandboxes, whose egress proxy substitutes the ARIZE_API_KEY secret
    into HTTPS request headers). Built by hand: arize-otel 0.14's register() crashes with Transport.HTTP."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider = TracerProvider(resource=Resource.create({"openinference.project.name": PROJECT}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint="https://otlp.arize.com/v1/traces",
        headers={"arize-space-id": os.environ["ARIZE_SPACE_ID"], "authorization": os.environ["ARIZE_API_KEY"]})))
    trace.set_tracer_provider(provider)


def enabled():
    return bool(trace and os.environ.get("ARIZE_SPACE_ID") and os.environ.get("ARIZE_API_KEY"))


def flush():
    if trace is not None and hasattr(trace.get_tracer_provider(), "force_flush"):
        trace.get_tracer_provider().force_flush()


class _NoSpan:
    def set_attribute(self, *a, **k): pass
    def set_attributes(self, *a, **k): pass
    def add_event(self, *a, **k): pass


def _value(v):
    """(text, mime type) for an input/output value."""
    if isinstance(v, str):
        return v, "text/plain"
    return json.dumps(v, default=str), "application/json"


def _flat(attrs):
    """OTel attributes must be primitives (or lists of them): JSON-encode everything else."""
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)) or (isinstance(v, (list, tuple)) and
                                                      all(isinstance(x, (str, bool, int, float)) for x in v)):
            out[k] = v
        else:
            out[k] = json.dumps(v, default=str)
    return out


@contextmanager
def span(name, kind="CHAIN", input=None, parent=None, metadata=None, **attrs):
    """A span of OpenInference kind CHAIN / LLM / TOOL / AGENT / GUARDRAIL / EVALUATOR. `parent` is a context from
    extract(); `metadata` shows as a JSON blob in Arize; other keyword attributes are set as-is."""
    tracer = setup()
    if tracer is None:
        yield _NoSpan()
        return
    a = {"openinference.span.kind": kind, "session.id": os.environ.get(SESSION_ENV)}
    if input is not None:
        a["input.value"], a["input.mime_type"] = _value(input)
    if metadata:
        a["metadata"] = json.dumps(metadata, default=str)
    a.update(attrs)
    with tracer.start_as_current_span(name, context=parent, attributes=_flat(a)) as s:
        yield s
        # A raised exception marks the span ERROR, but success is never marked: without this it exports as UNSET.
        if getattr(getattr(s, "status", None), "status_code", None) == trace.StatusCode.UNSET:
            s.set_status(trace.Status(trace.StatusCode.OK))


def set_output(s, value, **attrs):
    text, mime = _value(value)
    s.set_attributes(_flat({"output.value": text, "output.mime_type": mime, **attrs}))


def fail(s, message):
    """Mark a span as failed (exceptions raised inside a span are recorded automatically)."""
    if trace is not None and hasattr(s, "set_status"):
        s.set_status(trace.Status(trace.StatusCode.ERROR, message))


def event(name, **attrs):
    """An event on the current span."""
    if trace is not None:
        trace.get_current_span().add_event(name, _flat(attrs))


def _image_url(im, side=256):
    im = im.convert("RGB").copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@contextmanager
def llm_span(model_name, images, text, params, provider="local", system=None):
    """An LLM span for one VLM call: the labelled images and question as one user message."""
    m = "llm.input_messages.0.message"
    a = {"llm.model_name": model_name, "llm.provider": provider, "llm.system": system,
         "llm.invocation_parameters": json.dumps(params),
         f"{m}.role": "user"}
    k = 0
    with_images = os.environ.get("STANDUP_TRACE_IMAGES", "1") != "0" and enabled()
    for label, im in images:
        a[f"{m}.contents.{k}.message_content.type"] = "text"
        a[f"{m}.contents.{k}.message_content.text"] = label
        k += 1
        if with_images:
            a[f"{m}.contents.{k}.message_content.type"] = "image"
            a[f"{m}.contents.{k}.message_content.image.image.url"] = _image_url(im)
            k += 1
    a[f"{m}.contents.{k}.message_content.type"] = "text"
    a[f"{m}.contents.{k}.message_content.text"] = text
    with span("vlm.generate", "LLM", input=text, **a) as s:
        yield s


def set_llm_output(s, raw, prompt_tokens, completion_tokens, reasoning=None):
    """`reasoning`: the model's (summarized) thinking, shown in Arize as a reasoning part before the answer."""
    m = "llm.output_messages.0.message"
    a = {f"{m}.role": "assistant", f"{m}.content": raw,
         "llm.token_count.prompt": int(prompt_tokens),
         "llm.token_count.completion": int(completion_tokens),
         "llm.token_count.total": int(prompt_tokens) + int(completion_tokens)}
    if reasoning:
        a.update({f"{m}.contents.0.message_content.type": "reasoning",
                  f"{m}.contents.0.message_content.text": reasoning,
                  f"{m}.contents.1.message_content.type": "text",
                  f"{m}.contents.1.message_content.text": raw})
    set_output(s, raw, **a)


def inject():
    """The current trace context as a dict (stored in plan.json so generation spans join the plan's trace)."""
    carrier = {}
    if trace is not None:
        TraceContextTextMapPropagator().inject(carrier)
    return carrier


def extract(carrier):
    if trace is None or not carrier:
        return None
    return TraceContextTextMapPropagator().extract(carrier, context=otel_context.Context())
