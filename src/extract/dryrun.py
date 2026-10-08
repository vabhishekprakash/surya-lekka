"""Stubbed Bedrock replies for dry runs and tests. No network, no AWS account.

Calls still go through a real botocore client with Stubber, so every request
is checked against the bedrock-runtime service model.
"""

import copy
import json
from pathlib import Path

from .wire_schema import TOOL_NAME

DRY_RUN_WIRE = Path(__file__).parent / "dry_run_wire.json"


def load_dry_run_wire():
    return json.loads(DRY_RUN_WIRE.read_text(encoding="utf-8"))["tool_input"]


def remap_pages(tool_input, pages):
    """Copy of tool_input with any page outside pages moved to the first page."""
    out = copy.deepcopy(tool_input)

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("page"), int) and node["page"] not in pages:
                node["page"] = pages[0]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(out)
    return out


def converse_response(content, stop_reason="tool_use", input_tokens=1500, output_tokens=400):
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens,
                  "totalTokens": input_tokens + output_tokens},
        "metrics": {"latencyMs": 0},
    }


def tool_response(tool_input, name=TOOL_NAME, stop_reason="tool_use"):
    return converse_response([{"toolUse": {"toolUseId": "tooluse-dry-run", "name": name, "input": tool_input}}],
                             stop_reason)


def stubbed_client(service, region="ap-south-1"):
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(service, region_name=region, aws_access_key_id="dry-run",
                          aws_secret_access_key="dry-run")
    stubber = Stubber(client)
    stubber.activate()
    return client, stubber


class DryRunClient:
    """Answers each converse call with the synthetic extraction, pages moved
    into the batch."""

    def __init__(self, region="ap-south-1", tool_input=None):
        self._client, self._stubber = stubbed_client("bedrock-runtime", region)
        self._tool_input = tool_input or load_dry_run_wire()

    def converse(self, **request):
        texts = [b["text"] for b in request["messages"][0]["content"] if "text" in b]
        pages = [int(t.split()[1]) for t in texts if t.startswith("Page ")]
        self._stubber.add_response("converse", tool_response(remap_pages(self._tool_input, pages)))
        return self._client.converse(**request)
