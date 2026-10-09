import json
from types import SimpleNamespace

from agentkit.metering import METRIC_UNITS, NAMESPACE, Usage, emf_record, emit, usage_from_metrics


def metrics(usage, cycles=0, tools=None):
    tool_metrics = {name: SimpleNamespace(call_count=n) for name, n in (tools or {}).items()}
    return SimpleNamespace(accumulated_usage=usage, cycle_count=cycles, tool_metrics=tool_metrics)


def test_usage_from_metrics_reads_strands_fields():
    m = metrics(
        {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150, "cacheReadInputTokens": 7, "cacheWriteInputTokens": 3},
        cycles=3,
        tools={"calculator": 2, "lookup_order": 1},
    )
    u = usage_from_metrics("haiku-4-5", m, latency_ms=812)
    assert u == Usage("haiku-4-5", 120, 30, 7, 3, 812, 3, 3)


def test_missing_cache_keys_default_to_zero():  # Review Focus 4: Nova omits cache usage
    u = usage_from_metrics("nova-pro", metrics({"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}), 5)
    assert (u.cache_read_tokens, u.cache_write_tokens) == (0, 0)


def test_no_metrics_at_all_gives_zero_usage():  # Review Focus 4: failed call before any model response
    u = usage_from_metrics("nova-pro", None, 40)
    assert u == Usage("nova-pro", latency_ms=40)


def test_usage_round_trip_and_addition():
    u = Usage("m", 1, 2, 3, 4, 5, 6, 7)
    assert Usage.from_dict(u.to_dict()) == u
    assert Usage.from_dict({"model": "m", "input_tokens": None}) == Usage("m")
    assert (u + u).input_tokens == 2 and (u + u).model == "m"


def test_emf_record_shape():
    u = Usage("haiku-4-5", 10, 5, 0, 0, 100, 1, 0)
    rec = emf_record(
        "tools-agent", u, project="tmpl", errors=1, run_id="r1", case_id="c1", session_id="s1", timestamp_ms=1700000000000
    )
    directive = rec["_aws"]["CloudWatchMetrics"][0]
    assert rec["_aws"]["Timestamp"] == 1700000000000
    assert directive["Namespace"] == NAMESPACE
    assert directive["Dimensions"] == [["Project", "Agent", "Model"]]
    assert {m["Name"] for m in directive["Metrics"]} == set(METRIC_UNITS)
    assert rec["Project"] == "tmpl" and rec["Agent"] == "tools-agent" and rec["Model"] == "haiku-4-5"
    assert rec["InputTokens"] == 10 and rec["Errors"] == 1
    assert rec["run_id"] == "r1" and rec["case_id"] == "c1" and rec["session_id"] == "s1"


def test_emf_record_project_defaults_when_unset():  # local runs (G2) have no PROJECT env var
    assert emf_record("a", Usage("m"), errors=0)["Project"] == "default"


def test_emit_writes_one_json_line():
    lines = []
    emit({"a": 1}, write=lines.append)
    assert len(lines) == 1 and json.loads(lines[0]) == {"a": 1}
