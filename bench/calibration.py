"""Judge calibration pairs (validation gate G6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from bench.records import Verdict
from bench.suite import Case
from template_core.config import REPO_ROOT

CALIBRATION_FILE = REPO_ROOT / "bench" / "calibration.yaml"


class CalibrationPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    prompt: str
    expected: str
    good: str
    bad: str


@dataclass(frozen=True)
class CalibrationResult:
    id: str
    good_score: int | None
    bad_score: int | None
    ok: bool
    detail: str


def load_calibration(path: Path = CALIBRATION_FILE) -> list[CalibrationPair]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [CalibrationPair.model_validate(p) for p in data.get("pairs", [])]


def calibrate(grade: Callable[[Case, str], Verdict], pairs: list[CalibrationPair]) -> list[CalibrationResult]:
    results = []
    for pair in pairs:
        case = Case(id=pair.id, prompt=pair.prompt, expected=pair.expected)
        good, bad = grade(case, pair.good), grade(case, pair.bad)
        ok = good.score is not None and bad.score is not None and good.score >= 4 and bad.score <= 2
        detail = f"good={good.score} ({good.reason or good.error}); bad={bad.score} ({bad.reason or bad.error})"
        results.append(CalibrationResult(pair.id, good.score, bad.score, ok, detail))
    return results
