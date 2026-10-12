"""One span tree per run, posted once to Neatlogs (POST /v1/trace).

Step spans are kind TOOL; each LLM call is a kind LLM span with model, tokens and cache attributes.
A failed step gets status ERROR and an error string. Docs: https://docs.neatlogs.com/sdk/http-injection"""
import gzip
import json
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from .verdict import LLMCall

MAX_FIELD_CHARS = 20000
FIELD_CAPS = (MAX_FIELD_CHARS, 8000, 4000, 2000, 1000, 300)
# The ingest load balancer answers 403 to request bodies over about 8 KB (measured: 7,991 bytes passes,
# 8,510 fails; the docs say 50 MB), and one gzipped body just under 8,000 bytes was refused too. So bodies
# are gzipped, span text is shortened until the compressed body fits with margin, and a 403 is retried
# with shorter text.
MAX_BODY_BYTES = 6500
POST_TIMEOUT_S = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fit(value: Any, cap: int = MAX_FIELD_CHARS) -> Any:
    """Keep span input/output small enough to read in the dashboard."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= cap:
        return value
    return text[:cap] + f"... [{len(text) - cap} more chars]"


@dataclass
class Span:
    name: str
    kind: str
    input: Any = None
    output: Any = None
    status: str = "OK"
    error: str | None = None
    start: str = field(default_factory=_now)
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)  # model, tokens, tool_name, attributes, metadata
    children: list["Span"] = field(default_factory=list)

    def fail(self, error: str) -> None:
        self.status = "ERROR"
        self.error = error

    def to_payload(self, cap: int = MAX_FIELD_CHARS) -> dict[str, Any]:
        p: dict[str, Any] = dict(name=self.name, kind=self.kind, status=self.status, start=self.start, duration_ms=self.duration_ms)
        if self.input is not None:
            p["input"] = _fit(self.input, cap)
        if self.output is not None:
            p["output"] = _fit(self.output, cap)
        if self.error:
            p["error"] = self.error
        p.update(self.extra)
        if self.children:
            p["children"] = [c.to_payload(cap) for c in self.children]
        return p


def llm_span(call: LLMCall, name: str = "claude -p") -> Span:
    s = Span(name=name, kind="LLM", input=call.prompt, output=call.output, duration_ms=call.duration_ms)
    s.extra = dict(
        model=call.model,
        # prompt counts every input token, cached or not; the cache split is in the attributes
        tokens=dict(prompt=call.input_tokens + call.cache_read_tokens + call.cache_write_tokens, completion=call.output_tokens),
        attributes={
            "neatlogs.llm.provider": "anthropic",
            "neatlogs.llm.token_count.cache_read": call.cache_read_tokens,
            "neatlogs.llm.token_count.cache_write": call.cache_write_tokens,
        },
        metadata=dict(cost_usd=call.cost_usd, uncached_input_tokens=call.input_tokens),
    )
    if call.error:
        s.fail(call.error)
    return s


class Tracer:
    """Collects the spans of one run under a WORKFLOW root."""

    def __init__(self, name: str, project: str, input: Any = None) -> None:
        self.project = project
        self.root = Span(name=name, kind="WORKFLOW", input=input)
        self._t0 = time.monotonic()

    @contextmanager
    def step(self, name: str, input: Any = None, metadata: dict[str, Any] | None = None) -> Iterator[Span]:
        """A TOOL span around one pipeline step. An exception marks it ERROR and is re-raised.
        `metadata` (which the caller may keep filling inside the block) is what detection rules can read."""
        span = Span(name=name, kind="TOOL", input=input, extra=dict(tool_name=name))
        if metadata is not None:
            span.extra["metadata"] = metadata
        self.root.children.append(span)
        t0 = time.monotonic()
        try:
            yield span
        except Exception as e:
            span.fail(f"{type(e).__name__}: {e}")
            self.root.fail(f"{name} failed")
            raise
        finally:
            span.duration_ms = int((time.monotonic() - t0) * 1000)

    def payload(self, cap: int = MAX_FIELD_CHARS) -> dict[str, Any]:
        self.root.duration_ms = self.root.duration_ms or int((time.monotonic() - self._t0) * 1000)
        p = self.root.to_payload(cap)
        p["project"] = self.project  # root only
        return p

    def bodies(self, max_bytes: int = MAX_BODY_BYTES) -> Iterator[bytes]:
        """Gzipped JSON request bodies that fit, largest first; each later one has shorter span text."""
        fitting = [b for b in (encode(self.payload(cap)) for cap in FIELD_CAPS) if len(b) <= max_bytes]
        yield from dict.fromkeys(fitting or [encode(self.payload(FIELD_CAPS[-1]))])

    def body(self, max_bytes: int = MAX_BODY_BYTES) -> bytes:
        """The fullest gzipped body that fits."""
        return next(self.bodies(max_bytes))


def encode(payload: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False, default=str).encode(), mtime=0)


class Rejected(RuntimeError):
    """Neatlogs refused the body with HTTP 403, which is how its size limit shows up."""


def send(tracer: Tracer, endpoint: str, api_key: str) -> tuple[str, int]:
    """Deliver the run's trace exactly once, falling back to a smaller body if one is refused."""
    last: Rejected | None = None
    for body in tracer.bodies():
        try:
            return post(body, endpoint, api_key)
        except Rejected as e:
            last = e
    raise last or RuntimeError("no trace body to send")


def post(body: bytes, endpoint: str, api_key: str) -> tuple[str, int]:
    """POST the gzipped trace once; returns the trace_id Neatlogs generated and the span count it ingested.
    Errors never include the key."""
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/trace",
        data=body,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT_S) as r:
            reply = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise (Rejected if e.code == 403 else RuntimeError)(f"Neatlogs returned HTTP {e.code}: {' '.join(re.sub('<[^>]+>', ' ', e.read().decode(errors='replace')).split())[:200]}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise RuntimeError(f"Neatlogs post failed: {type(e).__name__}") from None
    if not reply.get("trace_id"):
        raise RuntimeError("Neatlogs response had no trace_id")
    return reply["trace_id"], int(reply.get("spans") or 0)
