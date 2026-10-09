from datetime import UTC, date, datetime

from validation.gates_final import g11_window, scan_for_account, second_prefix, tagged_resources


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
