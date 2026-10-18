"""Production code vs the independent oracle (tests/oracle.py).

1. Every provided lesson file: all 9 metrics and the result.
2. Every provided student: final state.
3. Thousands of random synthetic signals: event counting.
4. Random synthetic lessons: every metric.
"""
import csv
import json
import random

import pytest

from drivecoach import lesson_file, metrics, progress
from tests import oracle
from tests.conftest import ROOT

TOLERANCE = 1e-3   # production stores metrics rounded; the oracle keeps full precision


@pytest.fixture(scope="module")
def dataset():
    rules = json.loads((ROOT / "rules" / "rules_v1.json").read_text())
    files = sorted((ROOT / "sample_data" / "lessons").rglob("*.csv"))
    out = []
    for path in files:
        raw = path.read_bytes()
        header, rows = oracle.read_lesson(raw)
        out.append((path.name, header, oracle.metrics(header, rows), raw))
    return rules, out


def test_every_file_metrics_match_oracle(dataset):
    _, files = dataset
    for name, header, expected, raw in files:
        actual, _ = metrics.compute(lesson_file.parse(raw))
        for key, value in expected.items():
            assert actual[key] == pytest.approx(value, abs=TOLERANCE), (name, key)


def test_every_student_final_state_matches_oracle(dataset):
    rules, files = dataset
    by_id = {}
    for name, header, m, raw in files:
        by_id.setdefault(header["lesson_id"], (header, m))       # first upload wins (retry)
    with open(ROOT / "sample_data" / "students.csv", newline="") as f:
        students = list(csv.DictReader(f))
    for row in students:
        sid = row["student_id"]
        mine = [(h["started_at"], lid, h["lesson_type"], m) for lid, (h, m) in by_id.items()
                if h["student_id"] == sid]
        o_state, o_results = oracle.run_student(row, mine, rules)

        lessons = [dict(lesson_id=lid, lesson_type=t, started_at=s, metrics=m) for s, lid, t, m in mine]
        p_state, steps = progress.replay(progress.initial_state(row), lessons, [rules])
        assert {s["lesson_id"]: s["result"] for s in steps} == o_results, sid
        assert (p_state["program"], p_state["lessons_completed"], p_state["review_count"],
                p_state["next_lesson_type"], p_state["status"]) == \
               (o_state["program"], o_state["done"], o_state["reviews"], o_state["next"], o_state["status"]), sid
        if o_state["final"] is None:
            assert p_state["final_score"] is None
        else:
            assert p_state["final_score"] == pytest.approx(o_state["final"], abs=0.01)


@pytest.mark.parametrize("seed", range(3000))
def test_event_counting_matches_oracle_on_random_signals(seed):
    rnd = random.Random(seed)
    n = rnd.randint(1, 400)
    flags, value = [], rnd.random() < 0.5
    while len(flags) < n:                       # random run lengths around the 15 / 50 boundaries
        flags += [value] * rnd.choice([1, 5, 13, 14, 15, 16, 30, 48, 49, 50, 51, 80])
        value = not value
    flags = flags[:n]
    assert len(metrics.find_events(flags)) == oracle.count_events(flags), flags


@pytest.mark.parametrize("seed", range(40))
def test_random_lessons_match_oracle(seed):
    rnd = random.Random(seed)
    n = rnd.randint(500, 6000)
    unit = rnd.choice(["kmh", "mph"])
    lines = [f"# lesson_id: L-R{seed}", "# student_id: S-R", "# instructor_id: I-R", "# car_id: CAR-R",
             "# lesson_type: REGULAR", "# started_at: 2026-10-05T08:00:00Z", "# sample_rate_hz: 50",
             f"# speed_unit: {unit}", ",".join(lesson_file.EXPECTED_COLUMNS)]
    speed, limit = 0.0, 50
    for i in range(n):
        speed = max(0.0, min(140.0, speed + rnd.uniform(-1.5, 1.5)))
        if rnd.random() < 0.002:
            limit = rnd.choice([30, 50, 70, 90])
        a_long = rnd.choice([0.0, 0.0, 0.0, -3.5, -4.2, 3.0, 3.4]) if rnd.random() < 0.3 else rnd.uniform(-1, 1)
        a_lat = rnd.choice([0.0, 4.0, -4.0, 5.5]) if rnd.random() < 0.3 else rnd.uniform(-1, 1)
        lines.append(f"{i * 20},{speed:.2f},{a_long:.3f},{a_lat:.3f},0,{limit}")
    raw = ("\n".join(lines) + "\n").encode()
    header, rows = oracle.read_lesson(raw)
    expected = oracle.metrics(header, rows)
    actual, _ = metrics.compute(lesson_file.parse(raw))
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value, abs=TOLERANCE), (seed, key)
