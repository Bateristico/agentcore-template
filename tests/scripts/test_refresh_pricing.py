import importlib.util
import sys
from decimal import Decimal

import pytest

from template_core.config import REPO_ROOT
from template_core.price_list import PriceDiff


def load_script():
    spec = importlib.util.spec_from_file_location("refresh_pricing", REPO_ROOT / "scripts" / "refresh_pricing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("fetched", "code"),
    [(Decimal("1"), 0), (Decimal("2"), 0), (None, 1)],  # same, changed, not-found
)
def test_exit_code_is_1_when_any_price_is_not_found(monkeypatch, capsys, fetched, code):
    script = load_script()
    monkeypatch.setattr(script.boto3, "Session", lambda **kw: type("S", (), {"client": lambda self, *a, **k: None})())
    diffs = [PriceDiff("haiku-4-5", "input", Decimal("1"), Decimal("1")), PriceDiff("nova-pro", "output", Decimal("1"), fetched)]
    monkeypatch.setattr(script, "compare_prices", lambda table, client, region: diffs)
    monkeypatch.setattr(sys, "argv", ["refresh_pricing.py", "--region", "eu-central-1"])
    assert script.main() == code
    if code:
        assert "not found" in capsys.readouterr().out
