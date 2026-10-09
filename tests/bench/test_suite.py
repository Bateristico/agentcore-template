import pytest

from bench.suite import list_suites, load_suite
from template_core.config import ConfigError


def write(root, agent, name, text):
    folder = root / agent
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.yaml").write_text(text, encoding="utf-8")


def test_load_suite_with_defaults(tmp_path):
    write(tmp_path, "a", "basics", "suite: basics\ncases:\n  - id: c1\n    prompt: hi\n")
    s = load_suite("a", "basics", root=tmp_path)
    assert s.cases[0].id == "c1" and s.cases[0].expected is None
    assert (s.estimate.input_tokens, s.estimate.output_tokens) == (2000, 500)


def test_duplicate_case_ids_rejected(tmp_path):
    write(tmp_path, "a", "dup", "suite: dup\ncases:\n  - {id: c1, prompt: a}\n  - {id: c1, prompt: b}\n")
    with pytest.raises(ConfigError, match="duplicate case id"):
        load_suite("a", "dup", root=tmp_path)


def test_missing_suite_lists_available(tmp_path):
    write(tmp_path, "a", "basics", "suite: basics\ncases:\n  - {id: c1, prompt: a}\n")
    with pytest.raises(ConfigError, match="available: basics"):
        load_suite("a", "nope", root=tmp_path)
    assert list_suites("a", root=tmp_path) == ["basics"]
    assert list_suites("other", root=tmp_path) == []


def test_empty_prompt_rejected(tmp_path):
    write(tmp_path, "a", "bad", "suite: bad\ncases:\n  - {id: c1, prompt: ''}\n")
    with pytest.raises(ConfigError):
        load_suite("a", "bad", root=tmp_path)
