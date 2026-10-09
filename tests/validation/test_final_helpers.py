from datetime import UTC, date, datetime
from decimal import Decimal

from template_core.config import load_config
from template_core.costs import ReconRow
from validation.gates_final import (
    existing_stacks,
    g11_checks,
    g11_window,
    scan_for_account,
    second_prefix,
    second_prefix_problem,
    split_leftovers,
    tagged_resources,
)


def test_scan_for_account_skips_evidence_and_build_output(tmp_path):
    (tmp_path / "template_core").mkdir()
    (tmp_path / "template_core" / "a.py").write_text("ACCOUNT = '999988887777'\n")
    (tmp_path / "template_core" / "b.py").write_text("nothing here\n")
    (tmp_path / "validation" / "20261009T000000Z").mkdir(parents=True)
    (tmp_path / "validation" / "20261009T000000Z" / "state.json").write_text('{"account": "999988887777"}')
    (tmp_path / "infra" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "infra" / "node_modules" / "x" / "y.json").write_text("999988887777")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("validated in 999988887777")
    assert scan_for_account(tmp_path, "999988887777") == ["template_core/a.py"]


def test_second_prefix_fits_the_prefix_rule():
    assert second_prefix("tmpl") == "tmpl2"
    assert second_prefix("abcdefghijkl") == "abcdefghijk2"


class FakeTagging:
    def __init__(self, pages):
        self.pages = pages

    def get_resources(self, TagFilters, PaginationToken=""):
        idx = int(PaginationToken or 0)
        nxt = str(idx + 1) if idx + 1 < len(self.pages) else ""
        return {"ResourceTagMappingList": [{"ResourceARN": a} for a in self.pages[idx]], "PaginationToken": nxt}


def test_tagged_resources_paginates():
    assert tagged_resources(FakeTagging([["arn:a"], ["arn:b"]]), "tmpl") == ["arn:a", "arn:b"]


def test_g11_window_is_three_whole_completed_utc_days():
    start_day, end_day, start, end = g11_window(date(2026, 10, 9))
    assert (start_day, end_day) == (date(2026, 10, 6), date(2026, 10, 9))
    assert start == datetime(2026, 10, 6, tzinfo=UTC)
    assert end == datetime(2026, 10, 9, tzinfo=UTC)





def test_split_leftovers_matches_only_expected_bucket_any_partition():
    arns = ["arn:aws:s3:::tmpl-results", "arn:aws-cn:s3:::tmpl-results", "arn:aws:s3:::tmpl-results-other", "arn:aws:lambda:eu-west-1:1:function:f"]
    retained, left = split_leftovers(arns, "tmpl-results")
    assert retained == arns[:2]
    assert left == arns[2:]
    assert split_leftovers(arns, None) == ([], arns)


class FakeCfn:
    def list_stacks(self, NextToken=None):
        if NextToken is None:
            return {"StackSummaries": [{"StackName": "tmpl2-agent-x", "StackStatus": "CREATE_COMPLETE"}], "NextToken": "n"}
        return {"StackSummaries": [{"StackName": "tmpl2-old", "StackStatus": "DELETE_COMPLETE"}, {"StackName": "tmpl-platform", "StackStatus": "CREATE_COMPLETE"}]}


def test_existing_stack_guard_refuses_and_ignores_deleted():
    assert existing_stacks(FakeCfn(), "tmpl2") == ["tmpl2-agent-x"]
    assert existing_stacks(FakeCfn(), "other2") == []


def test_prefix_collision():
    assert second_prefix_problem("tmpl", "tmpl") is not None
    assert second_prefix_problem("tmpl", "tmpl2") is None


def _row(model, estimate, billed, within, note=""):
    return ReconRow("tools-agent", model, estimate, billed, None if within is None else 0.0, within, note)


def test_g11_checks_mapping():
    cfg = load_config()
    taggable = next(m for m, v in cfg.models.items() if v.billing_tag)
    untaggable = next((m for m, v in cfg.models.items() if not v.billing_tag), None)
    assert not all(c.ok for c in g11_checks([], cfg))  # vacuous run fails
    missing = g11_checks([_row(taggable, Decimal("1"), None, None, "no billed data yet")], cfg)
    assert any(not c.ok and "no billed data for taggable pair" in c.name for c in missing)
    good = g11_checks([_row(taggable, Decimal("1"), Decimal("1"), True)], cfg)
    assert all(c.ok for c in good)
    if untaggable:
        info = g11_checks([_row(untaggable, Decimal("1"), None, None, "no billing tag"), _row(taggable, Decimal("1"), Decimal("1"), True)], cfg)
        assert all(c.ok for c in info)
    warned = g11_checks([_row(taggable, Decimal("1"), Decimal("1"), True)], cfg, "x Warning: tags")
    assert any(c.warning and not c.ok for c in warned)
