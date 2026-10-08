"""One extraction call to Amazon Nova through the Bedrock Converse API.

The request forces the single tool extract_solar_quote through
toolConfig.toolChoice (shape checked against the botocore service model).
A reply counts only if it holds exactly one toolUse block for that tool and
the input passes local validation. Retries are off unless asked for.
"""

import base64
import json
import time

from .batching import DEFAULT_REQUEST_LIMIT_BYTES, MAX_IMAGES_PER_CALL
from .prompt import SYSTEM_PROMPT, TOOL_DESCRIPTION, page_label
from .wire_schema import TOOL_NAME, TOOL_SCHEMA, normalise_batch, validate

MAX_TOKENS = 4000
TEMPERATURE = 0
ACCEPTED_STOPS = ("tool_use", "end_turn")
# Error codes that stop a whole spike run instead of one batch.
STOP_RUN_CODES = {"AccessDeniedException", "ValidationException", "ThrottlingException",
                  "ServiceQuotaExceededException", "UnrecognizedClientException", "ExpiredTokenException"}


class ExtractionFailure(Exception):
    """A batch that produced no usable extraction. detail never holds document text."""

    def __init__(self, kind, detail, code=None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.code = kind, detail, code
        self.record = None  # timing, usage and raw response, when there was one

    @property
    def stops_run(self):
        return self.code in STOP_RUN_CODES


def make_client(region, retries=0, read_timeout=300, session=None):
    import boto3
    from botocore.config import Config

    config = Config(region_name=region, connect_timeout=10, read_timeout=read_timeout,
                    retries={"mode": "standard", "total_max_attempts": 1 + retries})
    return (session or boto3).client("bedrock-runtime", config=config)


def build_request(model_id, pages):
    content = [{"text": "Pages in this request: " + ", ".join(str(p.page) for p in pages)}]
    for p in pages:
        content.append({"text": page_label(p.page)})
        content.append({"image": {"format": "jpeg", "source": {"bytes": p.jpeg}}})
    return {
        "modelId": model_id,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
        "toolConfig": {
            "tools": [{"toolSpec": {"name": TOOL_NAME, "description": TOOL_DESCRIPTION,
                                    "inputSchema": {"json": TOOL_SCHEMA}}}],
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        },
    }


def request_size(request):
    """Bytes of the request serialised as JSON, with image bytes as base64."""
    def encode(obj):
        if isinstance(obj, (bytes, bytearray)):
            return base64.b64encode(obj).decode("ascii")
        raise TypeError(type(obj).__name__)
    return len(json.dumps(request, default=encode, separators=(",", ":")).encode("utf-8"))


def parse_response(response, pages):
    """The validated tool input, or ExtractionFailure."""
    stop = response.get("stopReason")
    content = ((response.get("output") or {}).get("message") or {}).get("content") or []
    tool_uses = [b["toolUse"] for b in content if "toolUse" in b]
    has_text = any(b.get("text", "").strip() for b in content)
    if stop == "max_tokens":  # a failure even when a tool block came back: it may be cut short
        raise ExtractionFailure("max_tokens", "the reply reached the token limit")
    if stop in ("content_filtered", "guardrail_intervened"):
        raise ExtractionFailure(stop, f"stop reason {stop}")
    if stop in ("malformed_model_output", "malformed_tool_use"):
        raise ExtractionFailure("malformed", f"stop reason {stop}")
    if stop not in ACCEPTED_STOPS:
        raise ExtractionFailure("unexpected_stop", f"stop reason {stop}")
    if not tool_uses:
        raise ExtractionFailure("unexpected_text" if has_text else "missing_tool_output",
                                "no tool call in the reply")
    if len(tool_uses) != 1:
        raise ExtractionFailure("multiple_tool_calls", f"{len(tool_uses)} tool calls in the reply")
    if tool_uses[0].get("name") != TOOL_NAME:
        raise ExtractionFailure("wrong_tool", "the reply called a different tool")
    cleaned, errors, ignored = validate(tool_uses[0].get("input"), [p.page for p in pages])
    if errors:
        raise ExtractionFailure("invalid_tool_input", "; ".join(errors[:10]))
    return cleaned, ignored


def extract_batch(client, model_id, pages, batch, size_limit=DEFAULT_REQUEST_LIMIT_BYTES):
    """Run one call. Returns a record with the contract fields, or raises
    ExtractionFailure with .record holding whatever came back."""
    if not 1 <= len(pages) <= MAX_IMAGES_PER_CALL:
        raise ValueError(f"a batch needs 1 to {MAX_IMAGES_PER_CALL} pages")
    request = build_request(model_id, pages)
    size = request_size(request)
    record = {"batch": batch, "model_id": model_id, "pages": [p.page for p in pages], "request_bytes": size}
    if size > size_limit:
        failure = ExtractionFailure("size_limit", f"request is {size} bytes, over the {size_limit} byte limit")
        failure.record = record
        raise failure

    from botocore.exceptions import BotoCoreError, ClientError

    started = time.perf_counter()
    try:
        response = client.converse(**request)
    except ClientError as e:
        error = e.response.get("Error", {})
        failure = ExtractionFailure("api_error", error.get("Message") or "", code=error.get("Code"))
        record["seconds"] = round(time.perf_counter() - started, 3)
        failure.record = record
        raise failure from None
    except BotoCoreError as e:
        failure = ExtractionFailure("connection", type(e).__name__)
        record["seconds"] = round(time.perf_counter() - started, 3)
        failure.record = record
        raise failure from None
    record.update({
        "seconds": round(time.perf_counter() - started, 3),
        "latency_ms": (response.get("metrics") or {}).get("latencyMs"),
        "usage": response.get("usage"),
        "stop_reason": response.get("stopReason"),
        "response": response,
    })
    try:
        cleaned, ignored = parse_response(response, pages)
    except ExtractionFailure as failure:
        failure.record = record
        raise
    record["ignored_keys"] = ignored
    record["contract"] = normalise_batch(cleaned, batch)
    return record
