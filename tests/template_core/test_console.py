import io
import sys

from template_core.console import utf8_stdout


def test_utf8_stdout_makes_symbols_printable(monkeypatch):
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    utf8_stdout()
    print("≈ æøå")
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").strip() == "≈ æøå"
