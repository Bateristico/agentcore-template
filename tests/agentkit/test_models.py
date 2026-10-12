import json

import pytest

from agentkit.models import KitConfigError, ModelChoice, ModelNotAllowed, load_model_profiles, resolve_model

ENV = {
    "MODEL_PROFILES": json.dumps({"haiku-4-5": "arn:aws:bedrock:eu-central-1:1:application-inference-profile/a", "k3": "global.moonshotai.kimi-k3"}),
    "DEFAULT_MODEL": "haiku-4-5",
}


def test_requested_alias_resolves():
    assert resolve_model("k3", ENV) == ModelChoice("k3", "global.moonshotai.kimi-k3")


def test_missing_request_uses_default():
    assert resolve_model(None, ENV).alias == "haiku-4-5"


def test_unknown_alias_lists_allowed():
    with pytest.raises(ModelNotAllowed) as err:
        resolve_model("gpt-x", ENV)
    assert err.value.allowed == ["haiku-4-5", "k3"]
    assert "gpt-x" in str(err.value) and "haiku-4-5, k3" in str(err.value)


@pytest.mark.parametrize("raw", [None, "", "not json", "[]", "{}", '{"a": 1}'])
def test_bad_model_profiles_env(raw):
    env = {} if raw is None else {"MODEL_PROFILES": raw}
    with pytest.raises(KitConfigError):
        load_model_profiles(env)


def test_no_default_and_no_request():
    with pytest.raises(KitConfigError, match="DEFAULT_MODEL"):
        resolve_model(None, {"MODEL_PROFILES": ENV["MODEL_PROFILES"]})
