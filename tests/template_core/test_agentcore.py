import io
import json

import pytest
from botocore.exceptions import ClientError

from agentkit.metering import Usage
from template_core.agentcore import AgentClient, new_session_id
from template_core.deployment import AgentEndpoint, Deployment

DEP = Deployment(
    prefix="t1",
    results_bucket="b",
    judge_model="m1",
    judge_profile="arn:judge",
    agents={"tools-agent": AgentEndpoint("tools-agent", "arn:rt", "rt-id", {"m1": "arn:p"}, "m1")},
)
USAGE = {"model": "m2", "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "latency_ms": 50, "cycles": 1, "tool_calls": 0}


class FakeAgentCore:
    def __init__(self, body: bytes = b"", error: Exception | None = None):
        self.body, self.error, self.kwargs = body, error, None

    def invoke_agent_runtime(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return {"response": io.BytesIO(self.body), "contentType": "application/json", "statusCode": 200}


def client_for(body=b"", error=None):
    fake = FakeAgentCore(body, error)
    return AgentClient(DEP, fake), fake


def test_success():
    client, fake = client_for(json.dumps({"output": "hi", "usage": USAGE, "error": None}).encode())
    r = client.invoke("tools-agent", "hello", model="m2", run_id="r1", case_id="c1")
    assert r.ok and r.output == "hi" and r.usage == Usage.from_dict(USAGE) and r.model == "m2"
    assert fake.kwargs["agentRuntimeArn"] == "arn:rt"
    assert len(fake.kwargs["runtimeSessionId"]) >= 33
    assert json.loads(fake.kwargs["payload"]) == {"prompt": "hello", "model": "m2", "run_id": "r1", "case_id": "c1"}


def test_optional_fields_are_omitted_and_session_id_is_kept():
    client, fake = client_for(json.dumps({"output": "x", "usage": USAGE, "error": None}).encode())
    sid = "s" * 40
    r = client.invoke("tools-agent", "hello", session_id=sid)
    assert json.loads(fake.kwargs["payload"]) == {"prompt": "hello"}
    assert fake.kwargs["runtimeSessionId"] == sid and r.session_id == sid


def test_non_ascii_prompt_is_utf8_json():  # Review Focus 3
    client, fake = client_for(json.dumps({"output": "ok", "usage": USAGE, "error": None}).encode())
    client.invoke("tools-agent", "Hvad koster ordren? æøå")
    assert "æøå".encode() in fake.kwargs["payload"]
    assert json.loads(fake.kwargs["payload"].decode("utf-8"))["prompt"].endswith("æøå")


def test_container_failure_is_returned_not_raised():  # Review Focus 1
    err = ClientError({"Error": {"Code": "RuntimeClientError", "Message": "container exited"}}, "InvokeAgentRuntime")
    client, _ = client_for(error=err)
    r = client.invoke("tools-agent", "hello")
    assert not r.ok and r.error == {"type": "RuntimeClientError", "message": "container exited"}
    assert r.usage is None and r.model == "m1"


@pytest.mark.parametrize("body", [b"Internal Server Error", b"", b"[1, 2]", b"\xff\xfe"])
def test_non_json_body_is_a_bad_response(body):  # Review Focus 1
    client, _ = client_for(body)
    r = client.invoke("tools-agent", "hello")
    assert r.error["type"] == "BadResponse" and not r.ok


def test_agent_reported_throttle_is_detected():
    body = json.dumps({"output": None, "usage": USAGE, "error": {"type": "ModelThrottledException", "message": "slow"}}).encode()
    client, _ = client_for(body)
    r = client.invoke("tools-agent", "hello")
    assert r.throttled and not r.ok and r.usage.input_tokens == 10


def test_unknown_agent():
    client, _ = client_for(b"{}")
    with pytest.raises(KeyError, match="not deployed"):
        client.invoke("nope", "hello")


def test_session_ids_are_long_enough_and_unique():
    a, b = new_session_id(), new_session_id()
    assert len(a) >= 33 and a != b
