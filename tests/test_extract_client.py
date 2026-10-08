import pytest

pytest.importorskip("boto3")

import botocore.session
from botocore.stub import ANY
from botocore.validate import validate_parameters

from extract.batching import MAX_IMAGES_PER_CALL, plan_batches
from extract.dryrun import converse_response, load_dry_run_wire, stubbed_client, tool_response
from extract.nova_client import (MAX_TOKENS, ExtractionFailure, build_request, extract_batch, make_client,
                                 request_size)
from extract.render import PageImage
from extract.wire_schema import TOOL_NAME

MODEL = "global.amazon.nova-2-lite-v1:0"
EXPECTED = {
    "modelId": MODEL, "system": ANY, "messages": ANY,
    "inferenceConfig": {"maxTokens": 4000, "temperature": 0},
    "toolConfig": {"tools": ANY, "toolChoice": {"tool": {"name": "extract_solar_quote"}}},
}


def pages(*numbers, size=1000):
    return [PageImage(n, b"\xff\xd8" + bytes(size), 100, 100, 150, 85) for n in numbers]


@pytest.fixture
def stub():
    client, stubber = stubbed_client("bedrock-runtime")
    yield client, stubber
    stubber.deactivate()


def failure_of(client, batch_pages=None, **kwargs):
    with pytest.raises(ExtractionFailure) as info:
        extract_batch(client, MODEL, batch_pages or pages(1, 2), 1, **kwargs)
    return info.value


# --- request ---------------------------------------------------------------------------

def test_request_shape():
    req = build_request(MODEL, pages(3, 4))
    assert req["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    assert [t["toolSpec"]["name"] for t in req["toolConfig"]["tools"]] == [TOOL_NAME]
    assert "outputConfig" not in req and "toolChoice" not in req
    assert req["inferenceConfig"] == {"maxTokens": MAX_TOKENS, "temperature": 0} and MAX_TOKENS == 4000
    content = req["messages"][0]["content"]
    assert [b.get("text") for b in content if "text" in b] == ["Pages in this request: 3, 4", "Page 3", "Page 4"]
    assert sum("image" in b for b in content) == 2


def test_request_matches_the_installed_service_model():
    model = botocore.session.get_session().get_service_model("bedrock-runtime").operation_model("Converse")
    tool_config = model.input_shape.members["toolConfig"]
    assert set(tool_config.members) >= {"tools", "toolChoice"}
    assert "tool" in tool_config.members["toolChoice"].members
    validate_parameters(build_request(MODEL, pages(1, 2)), model.input_shape)


def test_request_size_counts_base64():
    small, large = (request_size(build_request(MODEL, pages(1, size=n))) for n in (3000, 6000))
    assert large - small == 4000  # 3000 more bytes are 4000 more base64 characters
    assert small > 4000


def test_retries_off_by_default():
    assert make_client("ap-south-1").meta.config.retries == {"mode": "standard", "total_max_attempts": 1}
    assert make_client("ap-south-1", retries=2).meta.config.retries["total_max_attempts"] == 3


# --- replies ---------------------------------------------------------------------------

def test_success(stub):
    client, stubber = stub
    stubber.add_response("converse", tool_response(load_dry_run_wire()), EXPECTED)
    record = extract_batch(client, MODEL, pages(1, 2), 1)
    stubber.assert_no_pending_responses()
    (base,) = [f["field"] for f in record["contract"]["facts"] if f["name"] == "base_price"]
    assert base["value"]["parsed"] == "180000" and base["batch"] == 1
    assert record["usage"]["inputTokens"] == 1500 and record["stop_reason"] == "tool_use"
    assert record["pages"] == [1, 2] and record["request_bytes"] > 0 and record["seconds"] >= 0


def test_malformed_tool_input(stub):
    client, stubber = stub
    data = load_dry_run_wire()
    data["prices"][0]["raw"] = 180000
    stubber.add_response("converse", tool_response(data))
    f = failure_of(client)
    assert f.kind == "invalid_tool_input" and "input.prices[0].raw" in f.detail
    assert f.record["response"]["stopReason"] == "tool_use" and not f.stops_run


def test_max_tokens(stub):
    client, stubber = stub
    stubber.add_response("converse", tool_response(load_dry_run_wire(), stop_reason="max_tokens"))
    assert failure_of(client).kind == "max_tokens"


@pytest.mark.parametrize("response,kind", [
    (converse_response([{"text": "Here is the summary of the quote."}], "end_turn"), "unexpected_text"),
    (converse_response([], "end_turn"), "missing_tool_output"),
    (converse_response([{"text": "Blocked."}], "content_filtered"), "content_filtered"),
    (converse_response([], "guardrail_intervened"), "guardrail_intervened"),
    (tool_response(load_dry_run_wire(), stop_reason="max_tokens"), "max_tokens"),  # even with a tool block
])
def test_stop_reasons_and_text_replies(stub, response, kind):
    client, stubber = stub
    stubber.add_response("converse", response)
    f = failure_of(client)
    assert f.kind == kind and f.kind != "refused"


@pytest.mark.parametrize("response,kind", [
    (converse_response([{"toolUse": {"toolUseId": "a", "name": TOOL_NAME, "input": {}}},
                        {"toolUse": {"toolUseId": "b", "name": TOOL_NAME, "input": {}}}]), "multiple_tool_calls"),
    (tool_response(load_dry_run_wire(), name="other_tool"), "wrong_tool"),
    (tool_response(load_dry_run_wire(), stop_reason="malformed_tool_use"), "malformed"),
    (tool_response(load_dry_run_wire(), stop_reason="stop_sequence"), "unexpected_stop"),
])
def test_other_unusable_replies(stub, response, kind):
    client, stubber = stub
    stubber.add_response("converse", response)
    assert failure_of(client).kind == kind


@pytest.mark.parametrize("code,status,stops", [
    ("AccessDeniedException", 403, True),
    ("ThrottlingException", 429, True),
    ("ValidationException", 400, True),
    ("ModelErrorException", 424, False),
])
def test_api_errors(stub, code, status, stops):
    client, stubber = stub
    stubber.add_client_error("converse", service_error_code=code, service_message="synthetic",
                             http_status_code=status)
    f = failure_of(client)
    assert f.kind == "api_error" and f.code == code and f.stops_run is stops
    assert f.record["pages"] == [1, 2]


def test_size_limit_rejected_before_any_call(stub):
    client, stubber = stub
    f = failure_of(client, pages(1, size=10_000), size_limit=12_000)
    assert f.kind == "size_limit" and f.record["request_bytes"] > 12_000
    stubber.assert_no_pending_responses()


def test_batch_size_bounds(stub):
    client, _ = stub
    with pytest.raises(ValueError):
        extract_batch(client, MODEL, pages(*range(1, 7)), 1)


# --- batching --------------------------------------------------------------------------

def size_of(batch_pages):
    return request_size(build_request(MODEL, batch_pages))


def test_at_most_five_images_per_call():
    batches, rejected = plan_batches(pages(*range(1, 13)), size_of)
    assert [len(b) for b in batches] == [5, 5, 2] and rejected == []
    assert [p.page for b in batches for p in b] == list(range(1, 13))
    assert MAX_IMAGES_PER_CALL == 5


def test_batches_split_by_request_size():
    limit = size_of(pages(1, 2, size=30_000)) + 10
    batches, rejected = plan_batches(pages(1, 2, 3, 4, size=30_000), size_of, limit)
    assert [[p.page for p in b] for b in batches] == [[1, 2], [3, 4]] and rejected == []
    big = pages(2, size=200_000)
    batches, rejected = plan_batches(pages(1, size=30_000) + big + pages(3, size=30_000), size_of, limit)
    assert [[p.page for p in b] for b in batches] == [[1], [3]] and rejected == [2]
