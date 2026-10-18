"""Edge cases from the self-audit (audit/AUDIT_REPORT.md). Several of these
reproduced real defects before they were fixed; they are marked FINDING."""
import copy

import pytest

from drivecoach import lesson_file, metrics, progress, rules
from tests import oracle
from tests.test_core_units import HEADER, make_file, steady

E = metrics.find_events


def flags(*segments):
    out = []
    for value, length in segments:
        out += [value] * length
    return out


# ---------- Phase 3: event boundaries ----------

@pytest.mark.parametrize("segments, expected", [
    (((True, 14),), 0),
    (((True, 15),), 1),
    (((True, 16),), 1),
    (((True, 15), (False, 49), (True, 15)), 1),
    (((True, 15), (False, 50), (True, 15)), 2),
    (((True, 15), (False, 51), (True, 15)), 2),
    (((True, 15), (False, 10), (True, 3)), 1),                  # short run next to an event
    (((True, 20), (False, 100)), 1),                            # event at the start of the file
    (((False, 100), (True, 20)), 1),                            # event at the end of the file
    (((True, 15), (False, 40), (True, 15), (False, 40), (True, 15)), 1),   # transitive merge
    (((True, 15), (False, 60), (True, 15), (False, 60), (True, 15)), 3),   # distinct events
    (((True, 3000),), 1),                                       # one long event, not 200
])
def test_event_boundaries(segments, expected):
    f = flags(*segments)
    assert len(E(f)) == expected
    assert oracle.count_events(f) == expected


def test_threshold_values_are_inclusive_as_specified():
    rows = steady(200)
    for i in range(0, 15):
        rows[i][2] = -3.5           # exactly the brake threshold (<=)
    for i in range(100, 115):
        rows[i][3] = -4.0           # exactly the turn threshold (|x| >=)
    m, _ = metrics.compute(lesson_file.parse(make_file(rows)))
    assert m["harsh_brake_count"] == 1 and m["sharp_turn_count"] == 1


def test_over_limit_is_strictly_greater_than_limit_plus_5():
    rows = steady(100, speed=55, limit=50)          # exactly limit + 5: not over
    rows[0][1] = 55.01
    m, _ = metrics.compute(lesson_file.parse(make_file(rows)))
    assert m["pct_time_over_limit"] == 1.0


def test_no_moving_samples_gives_zero_average():
    m, _ = metrics.compute(lesson_file.parse(make_file(steady(100, speed=2))))  # 2 is not > 2
    assert m["avg_moving_speed_kmh"] == 0.0


def test_score_is_clamped_to_100_and_0():
    m, _ = metrics.compute(lesson_file.parse(make_file(steady(100))))
    assert m["lesson_score"] == 100.0


# ---------- FINDING: rounding before evaluation could cross a threshold ----------

def test_rounding_cannot_hide_a_threshold_breach(rules_v1):
    """119,999 samples (40 min) with 6,000 over the limit: 5.0000417 %, which is > 5.
    Rounded to 4 decimals it became 5.0 and the lesson PASSED instead of REVIEW."""
    n, over = 119_999, 6_000
    rows = [[i * 20, 60 if i < over else 40, 0.0, 0.0, 0, 50] for i in range(n)]
    m, _ = metrics.compute(lesson_file.parse(make_file(rows)))
    assert m["pct_time_over_limit"] > 5
    assert rules.evaluate(m, rules_v1)[0] == "REVIEW"


# ---------- FINDING: a few dropped samples were accepted silently ----------

def test_any_dropped_sample_is_rejected():
    """README says files with missing samples are rejected; the 1 % tolerance let a
    20-minute lesson lose up to ~600 samples without notice."""
    rows = steady(3000)
    del rows[1000:1010]                               # 0.2 s missing
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file(rows))
    assert err.value.code == "SAMPLE_GAP"


def test_header_sample_rate_must_match_timestamps():
    text = make_file(steady(500)).decode().replace("sample_rate_hz: 50", "sample_rate_hz: 25")
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(text.encode())
    assert err.value.code == "SAMPLE_GAP"


# ---------- FINDING: duplicate header keys, last one silently won ----------

def test_duplicate_header_key_is_rejected():
    text = make_file(steady(10)).decode().replace("# speed_unit: kmh", "# speed_unit: kmh\n# speed_unit: mph")
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(text.encode())
    assert err.value.code == "DUPLICATE_HEADER"


# ---------- Phase 2: other malformed input ----------

@pytest.mark.parametrize("raw, code", [
    (b"", "MISSING_HEADER"),
    (b"\xff\xfe\x00garbage", "NOT_UTF8"),
    (HEADER.format(unit="kmh").encode(), "NO_SAMPLES"),
])
def test_empty_and_binary_files(raw, code):
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(raw)
    assert err.value.code == code


@pytest.mark.parametrize("bad, code", [
    ("nan", "BAD_ROW"), ("inf", "BAD_ROW"), ("-inf", "BAD_ROW"), ("", "BAD_ROW"),
])
def test_non_finite_values(bad, code):
    rows = steady(10)
    text = make_file(rows).decode().replace("\n20,50,", f"\n20,{bad},", 1)
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(text.encode())
    assert err.value.code == code


def test_negative_speed_is_rejected():
    rows = steady(10)
    rows[3][1] = -1
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file(rows))
    assert err.value.code == "IMPLAUSIBLE_SPEED"


def test_time_going_backwards_is_rejected():
    rows = steady(10)
    rows[5][0] = rows[3][0]
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file(rows))
    assert err.value.code == "NON_MONOTONIC_TIME"


def test_extra_column_is_rejected():
    rows = steady(10)
    rows[2].append(1)
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file(rows))
    assert err.value.code == "BAD_ROW"


# ---------- Phase 4: rules ----------

def test_fail_takes_precedence_over_review(rules_v1):
    m = dict(duration_min=20, distance_km=1, avg_moving_speed_kmh=1, pct_time_over_limit=20,
             harsh_brake_count=3, harsh_accel_count=0, sharp_turn_count=0, max_lateral_g=0.1, lesson_score=1)
    assert rules.evaluate(m, rules_v1)[0] == "FAIL"


def test_evaluation_does_not_mutate_rules(rules_v1):
    before = copy.deepcopy(rules_v1)
    m = dict(duration_min=20, distance_km=1, avg_moving_speed_kmh=1, pct_time_over_limit=20,
             harsh_brake_count=3, harsh_accel_count=0, sharp_turn_count=0, max_lateral_g=0.1, lesson_score=1)
    rules.evaluate(m, rules_v1)
    progress.apply(progress.initial_state(dict(program="STANDARD", lessons_completed=0, review_count=2,
                                               next_lesson_type="REGULAR", status="ACTIVE")),
                   "REGULAR", 1, "REVIEW", rules_v1)
    assert rules_v1 == before


def test_rules_change_needs_no_code_change(rules_v1):
    stricter = dict(copy.deepcopy(rules_v1), version="v2", effective_from="2026-11-01")
    stricter["review_if_any"] = [{"metric": "harsh_brake_count", "op": ">=", "value": 1}]
    m = dict(duration_min=20, distance_km=1, avg_moving_speed_kmh=1, pct_time_over_limit=0,
             harsh_brake_count=1, harsh_accel_count=0, sharp_turn_count=0, max_lateral_g=0.1, lesson_score=95)
    lesson = lambda day: dict(lesson_id=f"L-{day}", lesson_type="REGULAR",
                              started_at=f"2026-{day}T08:00:00Z", metrics=m)
    _, steps = progress.replay(None, [lesson("10-31"), lesson("11-02")], [rules_v1, stricter])
    assert [(s["rules_version"], s["result"]) for s in steps] == [("v1", "PASS"), ("v2", "REVIEW")]


def test_effective_from_must_be_a_date(rules_v1):
    broken = dict(copy.deepcopy(rules_v1), effective_from="next month")
    with pytest.raises(rules.RulesError):
        rules.validate(broken)


# ---------- Phase 5: every state transition ----------

def st(**kw):
    s = progress.initial_state(dict(program="STANDARD", lessons_completed=0, review_count=0,
                                    next_lesson_type="REGULAR", status="ACTIVE"))
    s.update(kw)
    return s


def test_review_exactly_at_max_is_still_review(rules_v1):
    new, eff, _ = progress.apply(st(review_count=1), "REGULAR", 70, "REVIEW", rules_v1)
    assert eff == "REVIEW" and new["review_count"] == 2 and new["next_lesson_type"] == "PRACTICE"


def test_consecutive_reviews_escalate_on_the_third(rules_v1):
    s = st()
    results = []
    for _ in range(3):
        s, eff, _ = progress.apply(s, "REGULAR", 70, "REVIEW", rules_v1)
        results.append(eff)
    assert results == ["REVIEW", "REVIEW", "FAIL"]
    assert s["program"] == "EXTENDED" and s["review_count"] == 0


def test_fail_preserves_lessons_completed(rules_v1):
    new, _, _ = progress.apply(st(lessons_completed=7), "REGULAR", 10, "FAIL", rules_v1)
    assert new["lessons_completed"] == 7 and new["program"] == "EXTENDED"


def test_extended_completion_at_18(rules_v1):
    s = st(program="EXTENDED", lessons_completed=17)
    s, _, _ = progress.apply(s, "REGULAR", 77, "PASS", rules_v1)
    assert s["status"] == "COMPLETED" and s["final_score"] == 77.0


def test_lessons_after_instructor_review_change_nothing(rules_v1):
    s = st(program="EXTENDED", status="INSTRUCTOR_REVIEW")
    for result in ("PASS", "REVIEW", "FAIL"):
        new, eff, notes = progress.apply(s, "REGULAR", 90, result, rules_v1)
        assert new == s and eff == result


def test_invalid_preserves_everything(rules_v1):
    s = st(review_count=2, next_lesson_type="PRACTICE")
    new, eff, _ = progress.apply(s, "REGULAR", 0, "INVALID", rules_v1)
    assert new == s and eff == "INVALID"


def test_same_started_at_is_ordered_deterministically(rules_v1):
    base = dict(duration_min=20, distance_km=1, avg_moving_speed_kmh=1, pct_time_over_limit=0,
                harsh_brake_count=0, harsh_accel_count=0, sharp_turn_count=0, max_lateral_g=0.1)
    a = dict(lesson_id="L-B", lesson_type="REGULAR", started_at="2026-10-05T08:00:00Z",
             metrics=dict(base, lesson_score=90))
    b = dict(lesson_id="L-A", lesson_type="REGULAR", started_at="2026-10-05T08:00:00Z",
             metrics=dict(base, harsh_brake_count=3, lesson_score=85))
    _, s1 = progress.replay(st(), [a, b], [rules_v1])
    _, s2 = progress.replay(st(), [b, a], [rules_v1])
    assert [x["lesson_id"] for x in s1] == [x["lesson_id"] for x in s2] == ["L-A", "L-B"]


# ---------- FINDING: invalid initial state crashed the evaluator on every retry ----------

@pytest.mark.parametrize("field, value", [
    ("program", "PREMIUM"), ("status", "PAUSED"), ("next_lesson_type", "EXAM"),
    ("lessons_completed", "-1"), ("review_count", "x"),
])
def test_invalid_initial_state_is_rejected_early(field, value):
    row = dict(program="STANDARD", lessons_completed="3", review_count="0",
               next_lesson_type="REGULAR", status="ACTIVE")
    row[field] = value
    with pytest.raises(ValueError):
        progress.initial_state(row)


def test_provided_students_are_valid(student_rows):
    for row in student_rows.values():
        progress.initial_state(row)
