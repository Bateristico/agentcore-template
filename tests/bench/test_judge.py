import pytest
from botocore.exceptions import ClientError

from bench.judge import Judge, bias_warnings, build_prompt, parse_verdict
from bench.suite import Case
from template_core.config import parse_config


def converse_response(text, inp=100, out=20):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "usage": {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out},
        "metrics": {"latencyMs": 300},
        "stopReason": "end_turn",
    }


class FakeRuntime:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


CASE = Case(id="c1", prompt="What is 2+2?", expected="4", rubric="Must say 4")


def test_prompt_contains_expected_and_rubric_only_when_present():
    p = build_prompt(CASE, "four")
    assert "<expected>" in p and "Must say 4" in p and "four" in p
    bare = build_prompt(Case(id="c2", prompt="hi"), "hello")
    assert "<expected>" not in bare and "<rubric>" not in bare


@pytest.mark.parametrize(
    ("text", "score"),
    [('{"score": 5, "reason": "ok"}', 5), ('```json\n{"score": 2, "reason": "meh"}\n```', 2), ('Sure! {"score": 4, "reason": "x"} done', 4)],
)
def test_parse_verdict(text, score):
    assert parse_verdict(text)[0] == score


@pytest.mark.parametrize("text", ["no json", '{"score": "5"}', '{"score": 6}', '{"score": true}', '{"reason": "x"}'])
def test_parse_verdict_rejects(text):
    with pytest.raises(ValueError):
        parse_verdict(text)


def test_grade_success_records_usage_and_hides_model_identity():
    rt = FakeRuntime([converse_response('{"score": 5, "reason": "correct"}')])
    v = Judge("sonnet-4-6", "arn:judge", rt).grade(CASE, "4")
    assert (v.score, v.reason, v.error) == (5, "correct", None)
    assert v.usage.input_tokens == 100 and v.usage.model == "sonnet-4-6"
    assert rt.calls[0]["modelId"] == "arn:judge"
    assert "sonnet" not in rt.calls[0]["messages"][0]["content"][0]["text"]


def test_unparseable_twice_gives_judge_error_with_summed_usage():  # Review Focus 2
    rt = FakeRuntime([converse_response("I think it's fine"), converse_response("still no json")])
    v = Judge("j", "arn:judge", rt).grade(CASE, "4")
    assert v.score is None and "unparseable" in v.error and v.usage.input_tokens == 200


def test_retry_succeeds_on_second_attempt():
    rt = FakeRuntime([converse_response("oops"), converse_response('{"score": 3, "reason": "partial"}')])
    v = Judge("j", "arn:judge", rt).grade(CASE, "4")
    assert v.score == 3 and v.error is None and len(rt.calls) == 2


def test_client_error_is_a_judge_error():
    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse")
    v = Judge("j", "arn:judge", FakeRuntime([err])).grade(CASE, "4")
    assert v.score is None and v.error.startswith("ThrottlingException")


def test_bias_warnings_flag_same_family():
    cfg = parse_config(
        """
prefix: t1
region: eu-central-1
models:
  sonnet: { profile: eu.anthropic.claude-sonnet-4-6 }
  haiku: { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  nova: { profile: eu.amazon.nova-pro-v1:0 }
judge: sonnet
agents:
  a: { models: [sonnet, haiku, nova], default: haiku }
"""
    )
    warnings = bias_warnings(cfg, "sonnet", ["haiku", "nova", "gone"])
    assert len(warnings) == 1 and "haiku" in warnings[0] and "anthropic" in warnings[0]
