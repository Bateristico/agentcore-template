from botocore.exceptions import ClientError

from template_core.probe import ProbeResult, format_probe_table, probe_model, system_profile_arn

OK_CONVERSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 3, "outputTokens": 1, "totalTokens": 4},
    "metrics": {"latencyMs": 10},
}


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


class FakeRuntime:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.model_ids = []

    def converse(self, **kwargs):
        self.model_ids.append(kwargs["modelId"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeBedrock:
    def __init__(self, create_error=None):
        self.create_error = create_error
        self.created = []
        self.deleted = []

    def create_inference_profile(self, **kwargs):
        if self.create_error:
            raise self.create_error
        self.created.append(kwargs)
        return {"inferenceProfileArn": "arn:aws:bedrock:eu-central-1:111111111111:application-inference-profile/abc"}

    def delete_inference_profile(self, inferenceProfileIdentifier):
        self.deleted.append(inferenceProfileIdentifier)


def test_system_profile_arn():
    assert (
        system_profile_arn("eu-central-1", "111111111111", "eu.amazon.nova-pro-v1:0")
        == "arn:aws:bedrock:eu-central-1:111111111111:inference-profile/eu.amazon.nova-pro-v1:0"
    )


def test_converse_failure_skips_profile_check():
    runtime = FakeRuntime([client_error("AccessDeniedException", "no access")])
    bedrock = FakeBedrock()
    r = probe_model(bedrock, runtime, profile_id="eu.x.y", region="eu-central-1", account="111111111111")
    assert not r.converse_ok and "AccessDeniedException" in r.converse_error
    assert r.app_profile_ok is None
    assert bedrock.created == []


def test_profile_creation_unsupported_is_recorded():
    runtime = FakeRuntime([OK_CONVERSE])
    bedrock = FakeBedrock(create_error=client_error("ValidationException", "not supported"))
    r = probe_model(bedrock, runtime, profile_id="global.z.glm", region="eu-central-1", account="111111111111")
    assert r.converse_ok
    assert r.app_profile_ok is False and "not supported" in r.app_profile_error


def test_full_success_invokes_through_profile_and_deletes_it():
    runtime = FakeRuntime([OK_CONVERSE, OK_CONVERSE])
    bedrock = FakeBedrock()
    r = probe_model(bedrock, runtime, profile_id="eu.amazon.nova-pro-v1:0", region="eu-central-1", account="111111111111")
    assert r == ProbeResult("eu.amazon.nova-pro-v1:0", True, None, True, None)
    assert runtime.model_ids[1].endswith("application-inference-profile/abc")
    assert bedrock.created[0]["modelSource"]["copyFrom"].endswith("inference-profile/eu.amazon.nova-pro-v1:0")
    assert bedrock.deleted == ["arn:aws:bedrock:eu-central-1:111111111111:application-inference-profile/abc"]


def test_profile_deleted_even_when_invoke_through_it_fails():
    runtime = FakeRuntime([OK_CONVERSE, client_error("ValidationException", "bad")])
    bedrock = FakeBedrock()
    r = probe_model(bedrock, runtime, profile_id="global.x.y", region="eu-central-1", account="111111111111")
    assert r.app_profile_ok is False
    assert len(bedrock.deleted) == 1


def test_table_has_one_row_per_result():
    table = format_probe_table([ProbeResult("a", True, None, True, None), ProbeResult("b", False, "E: x", None, None)])
    assert table.count("\n| ") == 2  # two data rows (header has no leading newline; separator starts "|-")
    assert "| b | no |" in table
