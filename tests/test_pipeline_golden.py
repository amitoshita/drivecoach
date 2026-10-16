"""End-to-end check of the pure core against the provided sample data.

Expected values were derived by hand from the spec (see README "Results").
"""
import random

import pytest

from drivecoach import progress

EXPECTED_RESULTS = {
    "L-1001": "PASS", "L-1002": "PASS", "L-1003": "PASS",
    "L-1004": "PASS", "L-1005": "REVIEW", "L-1006": "PASS", "L-1007": "REVIEW", "L-1008": "PASS",
    "L-1009": "REVIEW", "L-1010": "FAIL", "L-1011": "PASS",
    "L-1012": "REVIEW", "L-1013": "PASS", "L-1014": "INVALID", "L-1015": "FAIL",
}

EXPECTED_FINAL = {
    "S-001": dict(program="STANDARD", lessons_completed=12, review_count=0,
                  next_lesson_type="REGULAR", status="COMPLETED", final_score=89.73),
    "S-002": dict(program="STANDARD", lessons_completed=5, review_count=2,
                  next_lesson_type="REGULAR", status="ACTIVE", final_score=None),
    "S-003": dict(program="EXTENDED", lessons_completed=7, review_count=0,
                  next_lesson_type="REGULAR", status="ACTIVE", final_score=None),
    "S-004": dict(program="EXTENDED", lessons_completed=10, review_count=2,
                  next_lesson_type="REGULAR", status="INSTRUCTOR_REVIEW", final_score=None),
}


def unique_lessons(records):
    """What the ingest step stores: first upload per lesson_id wins."""
    seen = {}
    for rec in records:
        seen.setdefault(rec["lesson_id"], rec)
    return list(seen.values())


def run_all(records, rules_v1, student_rows):
    lessons = unique_lessons(records)
    results, finals = {}, {}
    for sid, row in student_rows.items():
        mine = [l for l in lessons if l["student_id"] == sid]
        final, steps = progress.replay(progress.initial_state(row), mine, [rules_v1])
        finals[sid] = final
        results.update({s["lesson_id"]: s for s in steps})
    return results, finals


def check(results, finals):
    assert {k: v["result"] for k, v in results.items()} == EXPECTED_RESULTS
    for sid, expected in EXPECTED_FINAL.items():
        actual = {k: finals[sid][k] for k in expected}
        assert actual == expected, sid


def test_field_upload_order(parsed_lessons, rules_v1, student_rows):
    results, finals = run_all(list(parsed_lessons.values()), rules_v1, student_rows)
    check(results, finals)


@pytest.mark.parametrize("seed", range(5))
def test_any_upload_order_gives_same_answer(parsed_lessons, rules_v1, student_rows, seed):
    records = list(parsed_lessons.values())
    random.Random(seed).shuffle(records)
    results, finals = run_all(records, rules_v1, student_rows)
    check(results, finals)


def test_retry_file_is_the_same_recording(parsed_lessons):
    original, retry = parsed_lessons["L-1007.csv"], parsed_lessons["L-1007_retry1.csv"]
    assert original["lesson_id"] == retry["lesson_id"]
    assert original["fingerprint"] == retry["fingerprint"]


def test_counting_the_retry_twice_would_be_wrong(parsed_lessons, rules_v1, student_rows):
    """Documents why idempotency matters: double counting L-1007 fails S-002."""
    lessons = [l for l in parsed_lessons.values() if l["student_id"] == "S-002"]
    retry = dict(parsed_lessons["L-1007_retry1.csv"], lesson_id="L-1007-dup")
    final, _ = progress.replay(progress.initial_state(student_rows["S-002"]),
                               unique_lessons(lessons) + [retry], [rules_v1])
    assert final["program"] == "EXTENDED"


def test_mph_is_converted(parsed_lessons):
    m = parsed_lessons["L-1009.csv"]["metrics"]
    assert m["pct_time_over_limit"] == pytest.approx(9.69, abs=0.01)
    assert m["distance_km"] == pytest.approx(19.87, abs=0.01)


def test_short_lesson_is_invalid_even_with_bad_driving(parsed_lessons, rules_v1, student_rows):
    m = parsed_lessons["L-1014.csv"]["metrics"]
    assert m["duration_min"] == 8.0
    assert m["harsh_brake_count"] == 6  # would be a FAIL if it counted


def test_audit_step_is_complete(parsed_lessons, rules_v1, student_rows):
    results, _ = run_all(list(parsed_lessons.values()), rules_v1, student_rows)
    step = results["L-1010"]
    assert step["rules_version"] == "v1"
    assert len(step["rules_sha256"]) == 64
    assert step["metrics"]["pct_time_over_limit"] > 15
    assert step["state_change"]["program"] == {"from": "STANDARD", "to": "EXTENDED"}
    assert step["state_change"]["review_count"] == {"from": 2, "to": 0}
