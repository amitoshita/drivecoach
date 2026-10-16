import copy

import pytest

from drivecoach import lesson_file, metrics, progress, rules

HEADER = """# lesson_id: L-1
# student_id: S-1
# instructor_id: I-1
# car_id: CAR-1
# lesson_type: REGULAR
# started_at: 2026-10-05T08:00:00Z
# sample_rate_hz: 50
# speed_unit: {unit}
# uploaded_at: 2026-10-05T08:27:00Z
t_ms,speed,accel_long_ms2,accel_lat_ms2,steering_deg,speed_limit_kmh
"""


def make_file(rows, unit="kmh"):
    body = "\n".join(",".join(str(v) for v in r) for r in rows)
    return (HEADER.format(unit=unit) + body + "\n").encode()


def steady(n, speed=50, a_long=0.0, a_lat=0.0, limit=50):
    return [[i * 20, speed, a_long, a_lat, 0, limit] for i in range(n)]


# ---------- parsing / validation ----------

def test_parse_converts_mph():
    lesson = lesson_file.parse(make_file(steady(10, speed=10), unit="mph"))
    assert lesson.speed_kmh[0] == pytest.approx(16.09344)


@pytest.mark.parametrize("mutate, code", [
    (lambda t: t.replace("# speed_unit: kmh", "# speed_unit: knots"), "BAD_SPEED_UNIT"),
    (lambda t: t.replace("# lesson_type: REGULAR", "# lesson_type: EXAM"), "BAD_LESSON_TYPE"),
    (lambda t: t.replace("# student_id: S-1\n", ""), "MISSING_HEADER"),
    (lambda t: t.replace("# lesson_id: L-1", "# lesson_id: ../../etc"), "BAD_ID"),
    (lambda t: t.replace("t_ms,speed", "time,speed"), "BAD_COLUMNS"),
    (lambda t: t.replace("20,50,", "20,abc,", 1), "BAD_ROW"),
    (lambda t: t.replace("2026-10-05T08:00:00Z", "yesterday"), "BAD_TIMESTAMP"),
])
def test_rejects_bad_files(mutate, code):
    text = make_file(steady(10)).decode()
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(mutate(text).encode())
    assert err.value.code == code


def test_rejects_dropped_samples():
    rows = steady(200)
    del rows[50:100]
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file(rows))
    assert err.value.code == "SAMPLE_GAP"


def test_rejects_header_only_file():
    with pytest.raises(lesson_file.LessonFileError) as err:
        lesson_file.parse(make_file([]))
    assert err.value.code == "NO_SAMPLES"


def test_fingerprint_ignores_upload_metadata():
    a = make_file(steady(10))
    b = a.replace(b"08:27:00Z", b"09:41:12Z")
    assert lesson_file.parse(a).fingerprint == lesson_file.parse(b).fingerprint
    c = a.replace(b"20,50,", b"20,51,", 1)
    assert lesson_file.parse(a).fingerprint != lesson_file.parse(c).fingerprint


# ---------- events ----------

def flags(*segments):
    out = []
    for value, length in segments:
        out += [value] * length
    return out


def test_event_needs_15_samples():
    assert metrics.find_events(flags((True, 14))) == []
    assert metrics.find_events(flags((True, 15))) == [(0, 15)]


def test_runs_closer_than_50_samples_merge():
    assert len(metrics.find_events(flags((True, 15), (False, 49), (True, 15)))) == 1
    assert len(metrics.find_events(flags((True, 15), (False, 50), (True, 15)))) == 2


def test_short_runs_do_not_bridge_events():
    f = flags((True, 15), (False, 40), (True, 5), (False, 40), (True, 15))
    assert len(metrics.find_events(f)) == 2


def test_metrics_on_synthetic_lesson():
    rows = steady(3000, speed=60, limit=50)          # 1 minute, all over limit+5
    for i in range(100, 130):
        rows[i][2] = -4.0                           # one harsh brake, 30 samples
    lesson = lesson_file.parse(make_file(rows))
    m, events = metrics.compute(lesson)
    assert m["duration_min"] == 1.0
    assert m["distance_km"] == pytest.approx(1.0)
    assert m["pct_time_over_limit"] == 100.0
    assert m["harsh_brake_count"] == 1
    assert m["lesson_score"] == 0.0                 # 100 - 200 - 5, clamped
    assert events == [{"type": "HARSH_BRAKE", "at_s": 2.0, "duration_s": 0.6, "peak": -4.0, "speed_kmh": 60.0}]


# ---------- rules ----------

def test_rules_v1_is_valid(rules_v1):
    rules.validate(rules_v1)


@pytest.mark.parametrize("breakage", [
    lambda r: r["fail_if_any"].append({"metric": "nope", "op": ">", "value": 1}),
    lambda r: r["fail_if_any"].append({"metric": "max_lateral_g", "op": "=~", "value": 1}),
    lambda r: r["review_if_any"].append({"metric": "max_lateral_g", "op": ">", "value": "1"}),
    lambda r: r["programs"].pop("EXTENDED"),
    lambda r: r.pop("min_duration_min"),
])
def test_invalid_rules_are_rejected(rules_v1, breakage):
    broken = copy.deepcopy(rules_v1)
    breakage(broken)
    with pytest.raises(rules.RulesError):
        rules.validate(broken)


def base_metrics(**overrides):
    m = dict(duration_min=20, distance_km=10, avg_moving_speed_kmh=40, pct_time_over_limit=0,
             harsh_brake_count=0, harsh_accel_count=0, sharp_turn_count=0, max_lateral_g=0.3,
             lesson_score=100)
    m.update(overrides)
    return m


def test_rule_order(rules_v1):
    assert rules.evaluate(base_metrics(), rules_v1)[0] == "PASS"
    assert rules.evaluate(base_metrics(harsh_brake_count=3), rules_v1)[0] == "REVIEW"
    assert rules.evaluate(base_metrics(harsh_brake_count=6), rules_v1)[0] == "FAIL"
    assert rules.evaluate(base_metrics(duration_min=10, harsh_brake_count=6), rules_v1)[0] == "INVALID"
    assert rules.evaluate(base_metrics(pct_time_over_limit=5), rules_v1)[0] == "PASS"   # strict >
    assert rules.evaluate(base_metrics(max_lateral_g=0.65), rules_v1)[0] == "PASS"


def test_rules_selected_by_lesson_start(rules_v1):
    v2 = dict(copy.deepcopy(rules_v1), version="v2", effective_from="2026-11-01")
    assert rules.select([rules_v1, v2], "2026-10-31T23:00:00Z")["version"] == "v1"
    assert rules.select([rules_v1, v2], "2026-11-01T08:00:00Z")["version"] == "v2"
    with pytest.raises(rules.RulesError):
        rules.select([v2], "2026-10-05T08:00:00Z")


def test_rules_hash_is_order_independent(rules_v1):
    reordered = dict(reversed(list(rules_v1.items())))
    assert rules.canonical_hash(reordered) == rules.canonical_hash(rules_v1)


# ---------- state machine ----------

def state(**overrides):
    s = progress.initial_state(dict(program="STANDARD", lessons_completed=0, review_count=0,
                                    next_lesson_type="REGULAR", status="ACTIVE"))
    s.update(overrides)
    return s


def test_pass_on_practice_does_not_count(rules_v1):
    new, _, _ = progress.apply(state(next_lesson_type="PRACTICE"), "PRACTICE", 90, "PASS", rules_v1)
    assert new["lessons_completed"] == 0 and new["next_lesson_type"] == "REGULAR"


def test_review_beyond_max_becomes_fail(rules_v1):
    new, effective, _ = progress.apply(state(review_count=2), "REGULAR", 70, "REVIEW", rules_v1)
    assert effective == "FAIL"
    assert new["program"] == "EXTENDED" and new["review_count"] == 0


def test_fail_in_extended_needs_instructor(rules_v1):
    new, _, _ = progress.apply(state(program="EXTENDED"), "REGULAR", 10, "FAIL", rules_v1)
    assert new["status"] == "INSTRUCTOR_REVIEW"


def test_completion_and_final_score(rules_v1):
    s = state(lessons_completed=10)
    s, _, _ = progress.apply(s, "REGULAR", 80, "PASS", rules_v1)
    s, _, _ = progress.apply(s, "PRACTICE", 10, "PASS", rules_v1)      # practice not averaged
    s, _, _ = progress.apply(s, "REGULAR", 91, "PASS", rules_v1)
    assert s["status"] == "COMPLETED" and s["final_score"] == 85.5


def test_inactive_student_is_frozen(rules_v1):
    s = state(status="COMPLETED")
    new, effective, notes = progress.apply(s, "REGULAR", 10, "FAIL", rules_v1)
    assert new == s and effective == "FAIL" and "no state change" in notes[0]


def test_unknown_student_still_evaluated(rules_v1):
    lesson = dict(lesson_id="L-9", lesson_type="REGULAR", started_at="2026-10-05T08:00:00Z",
                  metrics=base_metrics())
    final, steps = progress.replay(None, [lesson], [rules_v1])
    assert final is None and steps[0]["result"] == "PASS"
