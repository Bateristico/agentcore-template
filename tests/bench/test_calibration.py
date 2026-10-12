from bench.calibration import calibrate, load_calibration
from bench.records import Verdict


def test_repo_calibration_set_loads():
    pairs = load_calibration()
    assert len(pairs) >= 4 and any(p.bad == "" for p in pairs)


def test_calibrate_passes_and_fails_per_pair():
    pairs = load_calibration()[:2]
    scores = {pairs[0].good: 5, pairs[0].bad: 1, pairs[1].good: 3, pairs[1].bad: 1}

    def grade(case, response):
        return Verdict(scores[response], "r", None, None)

    results = calibrate(grade, pairs)
    assert [r.ok for r in results] == [True, False]
    assert results[1].good_score == 3


def test_judge_error_counts_as_failure():
    pairs = load_calibration()[:1]
    results = calibrate(lambda c, r: Verdict(None, None, None, "unparseable"), pairs)
    assert results[0].ok is False and "unparseable" in results[0].detail
