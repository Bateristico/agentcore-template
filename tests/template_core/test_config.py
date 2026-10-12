from pathlib import Path

import pytest

from template_core.config import (
    REPO_ROOT,
    ConfigError,
    ModelCfg,
    load_config,
    parse_config,
)

FIXTURES = REPO_ROOT / "config" / "fixtures"
MINIMAL = (FIXTURES / "valid-minimal.yaml").read_text(encoding="utf-8")


def test_repo_config_is_valid():
    cfg = load_config()
    assert cfg.judge in cfg.models
    assert cfg.prefix == "tmpl"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("valid-*.yaml")), ids=lambda p: p.name)
def test_valid_fixtures_load(path: Path):
    load_config(path)


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("invalid-*.yaml")), ids=lambda p: p.name)
def test_invalid_fixtures_fail_with_message(path: Path):
    first = path.read_text(encoding="utf-8").splitlines()[0]
    expected = first.removeprefix("# expect:").strip()
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert expected in str(err.value)


def test_names_follow_spec():
    cfg = parse_config(MINIMAL.replace("  a: {", "  tools-agent: {"))
    assert cfg.runtime_name("tools-agent") == "t1_tools_agent"
    assert cfg.agent_stack_name("tools-agent") == "t1-agent-tools-agent"
    assert cfg.platform_stack_name == "t1-platform"
    assert cfg.profile_name("tools-agent", "m1") == "t1-tools-agent-m1"
    assert cfg.judge_profile_name == "t1-judge"
    assert cfg.dashboard_name == "t1-agent-costs"


def test_overrides_replace_values_and_none_is_ignored():
    cfg = parse_config(MINIMAL, prefix="t9", region=None)
    assert cfg.prefix == "t9"
    assert cfg.region == "eu-west-1"


def test_unquoted_account_number_is_accepted():
    cfg = parse_config(MINIMAL + "account: 123456789012\n")
    assert cfg.account == "123456789012"


@pytest.mark.parametrize(
    ("profile", "fm", "family"),
    [
        ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic"),
        ("global.amazon.nova-2-lite-v1:0", "amazon.nova-2-lite-v1:0", "amazon"),
        ("anthropic.claude-3-haiku-20240307-v1:0", "anthropic.claude-3-haiku-20240307-v1:0", "anthropic"),
    ],
)
def test_foundation_model_id_strips_geo_prefix(profile, fm, family):
    m = ModelCfg(profile=profile)
    assert m.foundation_model_id == fm
    assert m.family == family


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_is_a_config_error():
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse_config("prefix: [unclosed")
