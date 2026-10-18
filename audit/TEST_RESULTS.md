# Test results

All results below come from commands actually run on 2026-10-18, on branch `audit/self-review` (before it was merged and deployed).
Local: macOS, Python 3.9 venv (CI runs Python 3.12, the Lambda runtime). Live: AWS account
171379772437, us-east-1, deployment from `main` (`97245d6`).

## 1. Automated test suite

```
$ .venv/bin/pytest -q
3159 passed in 44.70s
```

| File | Tests | Result | What it covers |
|---|---|---|---|
| `test_against_oracle.py` | 3,042 | pass | production vs independent oracle: 16 files × 9 metrics, 4 student states, 3,000 random event patterns, 40 random lessons |
| `test_audit_edge_cases.py` | 47 | pass | event boundaries, thresholds, malformed input, rules, every state transition |
| `test_audit_resilience.py` | 11 | pass | ordering, idempotency, crash-and-retry at 5 points, optimistic lock, audit immutability |
| `test_core_units.py` | 33 | pass | parser, events, rules, state machine units |
| `test_handlers_aws.py` | 12 | pass | handlers end to end on moto |
| `test_pipeline_golden.py` | 11 | pass | golden results, field order + 5 shuffles |
| `test_rules_files.py` | 3 | pass | rules files valid, immutable, unique dates |

### Tests that were RED before a fix (8)

```
$ .venv/bin/pytest -q tests/test_audit_edge_cases.py      # before fixes
FAILED test_rounding_cannot_hide_a_threshold_breach
FAILED test_any_dropped_sample_is_rejected
FAILED test_duplicate_header_key_is_rejected
FAILED test_effective_from_must_be_a_date
FAILED test_invalid_initial_state_is_rejected_early[program-PREMIUM]
FAILED test_invalid_initial_state_is_rejected_early[status-PAUSED]
FAILED test_invalid_initial_state_is_rejected_early[next_lesson_type-EXAM]
FAILED test_invalid_initial_state_is_rejected_early[lessons_completed--1]
8 failed, 39 passed
```

Plus `test_field_order_end_to_end` (SigV4 assertion): `1 failed` with the old `api.py`, passing with the fix.
One intermediate fix (±50 % step tolerance) turned `test_header_sample_rate_must_match_timestamps` RED.
It was tightened to ±25 %.

### Audit tests (all passing)

```
tests/test_audit_edge_cases.py::test_event_boundaries[segments0-0]
tests/test_audit_edge_cases.py::test_event_boundaries[segments1-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments2-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments3-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments4-2]
tests/test_audit_edge_cases.py::test_event_boundaries[segments5-2]
tests/test_audit_edge_cases.py::test_event_boundaries[segments6-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments7-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments8-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments9-1]
tests/test_audit_edge_cases.py::test_event_boundaries[segments10-3]
tests/test_audit_edge_cases.py::test_event_boundaries[segments11-1]
tests/test_audit_edge_cases.py::test_threshold_values_are_inclusive_as_specified
tests/test_audit_edge_cases.py::test_over_limit_is_strictly_greater_than_limit_plus_5
tests/test_audit_edge_cases.py::test_no_moving_samples_gives_zero_average
tests/test_audit_edge_cases.py::test_score_is_clamped_to_100_and_0
tests/test_audit_edge_cases.py::test_rounding_cannot_hide_a_threshold_breach
tests/test_audit_edge_cases.py::test_any_dropped_sample_is_rejected
tests/test_audit_edge_cases.py::test_header_sample_rate_must_match_timestamps
tests/test_audit_edge_cases.py::test_duplicate_header_key_is_rejected
tests/test_audit_edge_cases.py::test_empty_and_binary_files[-MISSING_HEADER]
tests/test_audit_edge_cases.py::test_empty_and_binary_files[\xff\xfe\x00garbage-NOT_UTF8]
tests/test_audit_edge_cases.py::test_empty_and_binary_files[# lesson_id: L-1\n# student_id: S-1\n# instructor_id: I-1\n# car_id: CAR-1\n# lesson_type: REGULAR\n# started_at: 2026-10-05T08:00:00Z\n# sample_rate_hz: 50\n# speed_unit: kmh\n# uploaded_at: 2026-10-05T08:27:00Z\nt_ms,speed,accel_long_ms2,accel_lat_ms2,steering_deg,speed_limit_kmh\n-NO_SAMPLES]
tests/test_audit_edge_cases.py::test_non_finite_values[nan-BAD_ROW]
tests/test_audit_edge_cases.py::test_non_finite_values[inf-BAD_ROW]
tests/test_audit_edge_cases.py::test_non_finite_values[-inf-BAD_ROW]
tests/test_audit_edge_cases.py::test_non_finite_values[-BAD_ROW]
tests/test_audit_edge_cases.py::test_negative_speed_is_rejected
tests/test_audit_edge_cases.py::test_time_going_backwards_is_rejected
tests/test_audit_edge_cases.py::test_extra_column_is_rejected
tests/test_audit_edge_cases.py::test_fail_takes_precedence_over_review
tests/test_audit_edge_cases.py::test_evaluation_does_not_mutate_rules
tests/test_audit_edge_cases.py::test_rules_change_needs_no_code_change
tests/test_audit_edge_cases.py::test_effective_from_must_be_a_date
tests/test_audit_edge_cases.py::test_review_exactly_at_max_is_still_review
tests/test_audit_edge_cases.py::test_consecutive_reviews_escalate_on_the_third
tests/test_audit_edge_cases.py::test_fail_preserves_lessons_completed
tests/test_audit_edge_cases.py::test_extended_completion_at_18
tests/test_audit_edge_cases.py::test_lessons_after_instructor_review_change_nothing
tests/test_audit_edge_cases.py::test_invalid_preserves_everything
tests/test_audit_edge_cases.py::test_same_started_at_is_ordered_deterministically
tests/test_audit_edge_cases.py::test_invalid_initial_state_is_rejected_early[program-PREMIUM]
tests/test_audit_edge_cases.py::test_invalid_initial_state_is_rejected_early[status-PAUSED]
tests/test_audit_edge_cases.py::test_invalid_initial_state_is_rejected_early[next_lesson_type-EXAM]
tests/test_audit_edge_cases.py::test_invalid_initial_state_is_rejected_early[lessons_completed--1]
tests/test_audit_edge_cases.py::test_invalid_initial_state_is_rejected_early[review_count-x]
tests/test_audit_edge_cases.py::test_provided_students_are_valid
tests/test_audit_resilience.py::test_reverse_upload_order
tests/test_audit_resilience.py::test_retry_after_later_lessons_completed
tests/test_audit_resilience.py::test_same_event_three_times
tests/test_audit_resilience.py::test_key_with_spaces_and_plus
tests/test_audit_resilience.py::test_ingest_crash_then_retry[index_lesson]
tests/test_audit_resilience.py::test_ingest_crash_then_retry[put_lesson_once]
tests/test_audit_resilience.py::test_evaluate_crash_then_retry[append_evaluation]
tests/test_audit_resilience.py::test_evaluate_crash_then_retry[save_state]
tests/test_audit_resilience.py::test_evaluate_crash_then_retry[get_lessons]
tests/test_audit_resilience.py::test_concurrent_writer_is_detected_by_optimistic_lock
tests/test_audit_resilience.py::test_audit_history_cannot_be_overwritten
```

### Coverage

```
$ .venv/bin/python -m coverage run --source=src/drivecoach -m pytest -q && coverage report -m
api.py 93 %   authorizer.py 100 %   evaluate.py 98 %   ingest.py 96 %   lesson_file.py 94 %
log.py 100 %  metrics.py 100 %      progress.py 100 %  rules.py 92 %    store.py 95 %
TOTAL 748 statements, 31 missed, 96 %
```
Uncovered lines are error branches: the API's generic 500 path, some rules-validation messages,
BatchGetItem `UnprocessedKeys` paging, the 50 MB size cap, and a missing-timezone timestamp.

## 2. Static checks

```
$ terraform fmt -check -recursive terraform        → ok
$ terraform -chdir=terraform validate              → Success! The configuration is valid.
$ terraform -chdir=terraform/bootstrap validate    → Success! The configuration is valid.
$ terraform -chdir=terraform plan   (live state, read-only)
  # aws_lambda_function.fn["api"|"authorizer"|"evaluate"|"ingest"] will be updated in-place
  Plan: 0 to add, 4 to change, 0 to destroy.       → only the new code; no drift; students precondition passes
$ git grep -E "AKIA…|gho_…|aws_secret_access_key" $(git rev-list --all) | wc -l   → 0
$ git ls-files | grep -E "tfstate|backend.hcl" | wc -l                          → 0
```

## 3. Dataset: live AWS vs independent oracle

The live API (`GET /lessons/{id}`, `GET /students/{id}`) was read back and compared with `tests/oracle.py`
run on the original files (`students.csv`, `rules_v1.json`, all 16 lesson files, unmodified).
The data came from `scripts/upload_lessons.sh` in field order on 2026-10-17. `L-1007_retry1.csv` is the
same recording as `L-1007` and is stored only as a duplicate upload.

| Lesson | Student | Duration | Score | Result (live) | Result (oracle) | Metrics match | State change (live) |
|---|---|---|---|---|---|---|---|
| L-1001 | S-001 | 20.00 | 90.44 | PASS | PASS | yes | lessons_completed 9→10 |
| L-1002 | S-001 | 20.00 | 88.19 | PASS | PASS | yes | lessons_completed 10→11 |
| L-1003 | S-001 | 20.00 | 90.57 | PASS | PASS | yes | final_score None→89.73, lessons_completed 11→12, status ACTIVE→COMPLETED |
| L-1004 | S-002 | 20.00 | 91.74 | PASS | PASS | yes | lessons_completed 4→5 |
| L-1005 | S-002 | 20.00 | 72.00 | REVIEW | REVIEW | yes | next_lesson_type REGULAR→PRACTICE, review_count 0→1 |
| L-1006 | S-002 | 20.00 | 91.42 | PASS | PASS | yes | next_lesson_type PRACTICE→REGULAR |
| L-1007 | S-002 | 20.00 | 60.72 | REVIEW | REVIEW | yes | next_lesson_type REGULAR→PRACTICE, review_count 1→2 |
| L-1008 | S-002 | 20.00 | 91.83 | PASS | PASS | yes | next_lesson_type PRACTICE→REGULAR |
| L-1009 | S-003 | 20.00 | 68.61 | REVIEW | REVIEW | yes | next_lesson_type REGULAR→PRACTICE, review_count 1→2 |
| L-1010 | S-003 | 20.00 | 34.91 | FAIL | FAIL | yes | next_lesson_type PRACTICE→REGULAR, program STANDARD→EXTENDED, review_count 2→0 |
| L-1011 | S-003 | 20.00 | 90.52 | PASS | PASS | yes | lessons_completed 6→7 |
| L-1012 | S-004 | 20.00 | 65.29 | REVIEW | REVIEW | yes | next_lesson_type REGULAR→PRACTICE, review_count 1→2 |
| L-1013 | S-004 | 20.00 | 86.77 | PASS | PASS | yes | next_lesson_type PRACTICE→REGULAR |
| L-1014 | S-004 | 8.00 | 13.77 | INVALID | INVALID | yes | none |
| L-1015 | S-004 | 20.00 | 49.91 | FAIL | FAIL | yes | status ACTIVE→INSTRUCTOR_REVIEW |

| Student | Program | Status | Lessons completed | Review count | Final score | Matches oracle |
|---|---|---|---|---|---|---|
| S-001 | STANDARD | COMPLETED | 12 | 0 | 89.73 | yes |
| S-002 | STANDARD | ACTIVE | 5 | 2 |  | yes |
| S-003 | EXTENDED | ACTIVE | 7 | 0 |  | yes |
| S-004 | EXTENDED | INSTRUCTOR_REVIEW | 10 | 2 |  | yes |

MISMATCHES: 0

Chronological processing: each student's lessons are listed in `started_at` order above. The
`state_before` of every audit record equals the `state_after` of the previous lesson. That holds by
construction (`progress.replay`) and is checked by the oracle comparison.

Earlier live runs (2026-10-17): the field order and an all-at-once shuffled burst (`make upload-burst`)
gave identical tables. The burst produced 12 re-evaluations, each linking to the record it superseded.

## 4. Live API matrix

| Case | Expected | Actual |
|---|---|---|
| valid key, GET /students/S-002 | 200 | 200 |
| valid key, GET /lessons/L-1015 | 200 | 200 |
| no key | 401 | 401 |
| empty key | 403 | 403 |
| wrong key | 403 | 403 |
| starter kit's leaked key | 403 | 403 |
| key in query string, not header | 401 | 401 |
| unknown student S-999 | 404 | 404 |
| unknown lesson L-9999 | 404 | 404 |
| invalid id `S-001' OR 1=1` | 400 | 400 |
| path traversal `../../etc` | 400 | 400 |
| POST / DELETE | 404 | 404 |
| unrouted /admin | 404 | 404 |
| PII strings in 6 responses | 0 | 0 |
| presigned raw-file link | 200 | **400 (bug P2-1)** |
| same object without signature | 403 | 403 |

P2-1 proof against the live bucket (local SigV4 signing, read-only):
```
default (as deployed)    HTTP 400
SigV4 (the fix)          HTTP 200  first line: # lesson_id: L-1001
```

## 5. Live operations

| Check | Result |
|---|---|
| Budget | `drivecoach-10usd` 10 USD monthly; alerts ACTUAL 85 %, ACTUAL 100 %, FORECASTED 100 % |
| Encryption | S3 `aws:kms`; DynamoDB lessons/evaluations/students/rules `KMS` |
| Public access block | True ×4; anonymous GET → 403 |
| IAM | ingest 14, evaluate 11, api 8, authorizer 4 explicit actions; 0 wildcard actions; 0 managed policies |
| Lambda (3 days, CloudWatch REPORT lines) | ingest 55 inv., p50 651 ms, max 2085 ms, 135/1024 MB · evaluate 50 inv., p50 78 ms, 101/512 MB · api 153 inv., p50 20 ms · authorizer 12 inv., p50 1430 ms (8 cold) |
| Logs | 462 app lines, 462 valid JSON, 0 non-JSON; 0 PII hits in 5 log groups (incl. API access logs) |
| DLQ alarm | OK → ALARM 09:25 → SNS publish succeeded → OK 09:30; **no subscriber** at audit time (P2-3). Resolved after this run, outside these tests: see the audit report's post-deployment status |
| DLQ replay | first attempt moved 0 (no destination; message had no source queue) → Makefile fixed → moved 1, processed, idempotent |

## 6. Edge-case coverage map

| Area | Covered by |
|---|---|
| Events 14/15/16 samples, 49/50/51 gap, start/end of file, transitive merge, distinct events, one long run | `test_event_boundaries` + 3,000 random oracle cases |
| Thresholds `<= -3.5`, `>= 3.0`, `|x| >= 4.0`, over-limit strictly `> limit+5`, moving `> 2` | `test_threshold_values_are_inclusive_as_specified`, `test_over_limit_is_strictly…`, `test_no_moving_samples…` |
| Score clamp | `test_score_is_clamped_to_100_and_0`, synthetic lesson clamped to 0 |
| Empty / binary / header-only / NaN / inf / blank / negative speed / backwards time / extra column / duplicate header / wrong rate / dropped sample | `test_audit_edge_cases.py` |
| Missing student_id, bad ids, unit, type, timestamp | `test_rejects_bad_files` |
| Unknown student | `test_lesson_of_unknown_student_is_kept_and_evaluated` |
| Rule order, FAIL over REVIEW, INVALID over all, no mutation, v1→v2 by date without code | `test_audit_edge_cases.py` |
| Every state transition (PASS reg/practice, REVIEW below/at/above max, 3 consecutive reviews, FAIL std/ext, INVALID, complete std/ext, after COMPLETED / INSTRUCTOR_REVIEW, same started_at) | `test_core_units.py` + `test_audit_edge_cases.py` |
| Reverse / random / simultaneous / late / retry-after-later | `test_audit_resilience.py`, `test_handlers_aws.py`, golden |
| Same event ×3, S3 key with spaces and `+` | `test_audit_resilience.py` |
| Crash after: put lesson, index lesson, read lessons, append audit, save state | `test_ingest_crash_then_retry`, `test_evaluate_crash_then_retry` |
| Concurrent writer | `test_concurrent_writer_is_detected_by_optimistic_lock` |

## 7. Not executed

- `make destroy` / a redeploy from zero: destructive, not authorized during the audit.
- Live upload of a malformed or conflicting file (quarantine path): would change live data. Covered on moto.
- Live rules-v2 switch: would change live data. Covered by unit tests.
- Load test with many or large files.
