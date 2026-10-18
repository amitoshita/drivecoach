# DriveCoach self-audit report

Branch: `audit/self-review` (forked from `main` at `97245d6`; `main` is unchanged and is what is deployed).
Date: 2026-10-18. Auditor: Claude (the same agent that wrote the code), treating it as someone else's submission.

The assignment text is not in the repository on purpose (it is confidential). It was audited from the
copy given in the working session.

## Executive summary

**Readiness: YES WITH CAVEATS.**

- The provided dataset produces correct results, verified three independent ways. (1) The production
  code. (2) An independent re-implementation of the spec that shares no code (`tests/oracle.py`).
  (3) The live AWS deployment, read back through the authenticated API. All 15 lessons and all 4 final
  student states agree (0 mismatches).
- Every Required and Advanced item is implemented. Level 3 is implemented or designed as documented.
  Elixir (bonus) is missing.
- The audit found **9 new defects**. 7 are fixed on this branch with a regression test that failed
  before the fix. 2 need a decision or a deploy (alarm subscriber, auto-rebuild on enrolment).
  **No defect changes any result on the provided data.** The most visible one (P2-1) broke the raw-file
  download link on the live API.
- Caveats before submission: the candidate's name is missing from the README; the branch fixes are
  not deployed yet; alarms have no subscriber; Elixir is not done.

## Requirement traceability matrix

Status: PASS = implemented and verified by execution · PARTIAL · FAIL · MISSING · UNVERIFIED.

| # | Requirement | Status | Location | Verified by | Issues |
|---|---|---|---|---|---|
| R1 | S3 upload triggers a Lambda that validates and parses | PASS | `terraform/storage.tf` (notification), `queues.tf`, `handlers/ingest.py`, `lesson_file.py` | live upload of 16 files; moto tests; 47 edge-case tests | — |
| R2 | Metrics of section 5, stored per lesson | PASS | `metrics.py`; `lessons` table | oracle on 16 files + 40 random lessons; live read-back vs oracle | P1-1 rounding (fixed) |
| R3 | Rules from versioned config, not hard-coded | PASS | `rules/rules_v1.json` → `rules` table; `rules.py`, `store.load_rule_sets` | tests: selection by date, v2 without code change, validation | P3-2 date not validated (fixed) |
| R4 | Audit record: rules version, metrics, result, state change, timestamp | PASS | `evaluations` table, `handlers/evaluate.py` | live `GET /lessons/{id}` audit_trail; moto test of history | — |
| R5 | Student state per section 7 | PASS | `progress.py` | oracle state machine; 16 transition tests; live states | P2-4 invalid initial state (fixed) |
| R6 | IaC, one command to deploy and destroy | PASS (deploy) / UNVERIFIED (destroy) | `Makefile`, `terraform/` | `make deploy` live (67 resources), read-only `plan` = no drift | `make destroy` not run (forbidden without authorization) |
| R7 | GitHub Actions: tests + deploy | PASS | `.github/workflows/ci.yml` | green run 39103098043: tests → plan → apply → smoke test | P3-5 no review gate |
| R8a | Private, encrypted storage | PASS | `storage.tf`, `dynamodb.tf` | live: SSE-KMS on bucket + 4 tables, public-access block 4×True, anonymous GET 403 | — |
| R8b | Least privilege per function | PASS | `terraform/lambda.tf` | live IAM read: 4–14 explicit actions per role, no `*`, no managed policies | P3-4 ingest may update any `students` item |
| R8c | No secrets in the repository | PASS | `.gitignore`, `api.tf` | `git grep` of all history for AWS/GitHub keys: 0; no state files tracked | starter key in commit 1, documented as leaked |
| R8d | Personal data protected, never logged | PASS | `dynamodb.tf` (only non-PII columns), `log.py` | live scan of 5 log groups for names/IDs/phones/emails/DOB: 0; API responses: 0 | — |
| R9 | `upload_lessons.sh` results in README | PASS | `README.md` Results | live run 2026-10-17 + read-back vs oracle 2026-10-18 | — |
| A1 | DLQ + replay | PASS | `queues.tf`, `make replay-dlq` | live: message → DLQ → alarm ALARM → replay → processed → OK | fixed replay target during audit |
| A2 | Ordering: all at once / out of order | PASS | `progress.replay`, FIFO per student, consistent lesson index | live burst (identical results, 12 re-evaluations); moto: reverse, shuffled, late, retry-after | — |
| A3 | Structured logs + ≥1 alarm | PASS | `log.py`, `observability.tf` | live: 462 app log lines, 100 % JSON; 10 alarms; alarm fired and published | P2-3 no subscriber |
| A4 | Authenticated API: GET /students/{id}, /lessons/{id} | PASS | `api.tf`, `handlers/api.py`, `authorizer.py` | live matrix of 14 cases (200/401/403/404/400) | P2-1 raw link broken (fixed, not deployed) |
| A5 | Cost estimate 200 cars × 3/day | PASS | README | recomputed with 30 days and measured Lambda durations | estimate, not a bill |
| C1 | Rules v2 without code redeploy, past decisions explainable | PARTIAL | `rules.py` select-by-date, `published.json` lock, hash in audit | unit tests (v1/v2 switch by date, immutability guard) | not exercised live; retroactive re-evaluation only designed |
| C2 | Instructor feature + rationale | PASS (timeline, coaching) / FAIL→fixed (raw link) | `metrics.py` events, `api.py` coaching | live API returns events + coaching | P2-1 |
| C3 | Thinking: late files / 100× | PASS | README Level 3 | review | — |
| B1 | Elixir | MISSING | — | — | bonus only |
| S1 | README starts with full name | FAIL | `README.md` line 3 | inspection | placeholder `_FULL NAME_` |
| S2 | README covers diagram, deploy/destroy, results, assumptions, another week, starter-kit findings | PASS | `README.md` | inspection | — |
| S3 | Zip with `.git`, live URL + how to call in email | MISSING (not yet requested) | — | — | to do at submission |

## Findings

Each finding has evidence and a regression test that failed before the fix ("RED → GREEN").

### P0: none open
Found and fixed **before** this audit (listed for completeness, see README §12–13):
FIFO dedup ID per lesson content made SQS drop re-uploads within 5 minutes (found live), and an
eventually consistent GSI read could lose a lesson in a burst.

### P1
**P1-1 Rounding before evaluation could cross a threshold. FIXED.**
`metrics.py` rounded to 4 decimals and then evaluated. 119,999 samples with 6,000 over the limit =
5.0000417 % → stored 5.0 → `> 5` false → PASS instead of REVIEW. Needs ≥ 100,000 samples (≈ 33 min),
so no sample file is affected (oracle agrees on all 16). Fix: `PRECISION = 10`.
Test: `test_audit_edge_cases.py::test_rounding_cannot_hide_a_threshold_breach` (RED → GREEN).

### P2
**P2-1 Raw-file download link always failed. FIXED on branch, NOT DEPLOYED.**
Live: `GET /lessons/L-1001` → `raw_file_url` → HTTP 400 `InvalidArgument: Requests specifying Server Side
Encryption with AWS KMS managed keys require AWS Signature Version 4`. boto3 signed with SigV2. moto
does not enforce this, so the earlier test only checked `startswith("https://")`. Fix: S3 client with
`Config(signature_version="s3v4")` (`handlers/api.py`). Proof on the live bucket: SigV2 → 400, SigV4 → 200
with the file content. Test asserts `X-Amz-Algorithm=AWS4-HMAC-SHA256` (RED → GREEN).

**P2-2 Dropped samples accepted silently. FIXED.**
`lesson_file._validate_samples` compared the total span with a tolerance of max(step, 1 %). A 20-minute
file could miss ~600 samples, which under-reports duration and distance. This contradicted the README,
which said missing samples are rejected. Fix: every step must be within ±25 % of `1000/sample_rate_hz`.
This also rejects a wrong rate header. An intermediate ±50 % version let a 2× rate mismatch through; it was
caught by `test_header_sample_rate_must_match_timestamps`. Tests: `test_any_dropped_sample_is_rejected` (RED → GREEN).

**P2-3 Alarms notify nobody. OPEN (needs an email + deploy).**
Live alarm history: `drivecoach-ingest-dlq-not-empty` OK → ALARM (09:25) → "Successfully executed action
…drivecoach-alarms" → OK (09:30). `list-subscriptions-by-topic` is empty because `alarm_email` defaults to "".
Fix: `terraform apply -var alarm_email=…` and confirm the subscription email.

**P2-4 Invalid initial state crashed the evaluator on every retry. FIXED.**
A `students.csv` row with an unknown program raised `KeyError` deep in `progress.apply` on each of 5
retries, then went to the DLQ. Fix: `progress.validate_state` (replay) + a Terraform `precondition` on the
seed, so it fails at `terraform plan`. Verified: the live `plan` passes the precondition on the real file.
Tests: `test_invalid_initial_state_is_rejected_early[4 cases]` (RED → GREEN).

**P2-5 Lesson before enrolment is never re-evaluated automatically. OPEN (documented).**
Design gap: enrolment (Terraform item) does not enqueue a rebuild. Workaround documented in the README
(send `{"student_id": …}` to the evaluate queue).

### P3
- **P3-1 Duplicate header keys: last value silently won. FIXED** (`DUPLICATE_HEADER`, RED → GREEN).
- **P3-2 `effective_from` not validated as a date. FIXED** (RED → GREEN).
- **P3-3 Quarantine copied the newest object version, not the one inspected. FIXED** (`CopySource` carries
  `VersionId`). Verified by inspection plus the existing quarantine tests. moto cannot reproduce the race.
- **P3-4 Ingest role may `UpdateItem` any item of `students`**, not only `record=LESSONS`. IAM cannot
  condition on a sort-key value. OPEN, accepted. Alternative: a separate index table.
- **P3-5 CI deploys every push to `main` without review.** OPEN. Recommend branch protection + a required
  reviewer on the `production` environment (not available for private repos on the free plan).
- **P3-6 Authorizer median 1.4 s** (mostly cold starts + SSM fetch; 8 of 12 calls were cold). Mitigated by the
  5-minute API Gateway cache. OPEN.
- **P3-7 Bootstrap stack state is local** (`terraform/bootstrap/terraform.tfstate`, git-ignored). OPEN, documented.
- **P3-8 Terraform state contains the generated API key** in an encrypted, private, versioned bucket. Accepted, documented.
- **P3-9 Rejected-file logs include up to 200 characters of header text** (e.g. a bad unit). This is box data, not
  students.csv data. Accepted.

## Tests executed (all by execution, 2026-10-18)

| Suite | Result |
|---|---|
| `pytest` (all) | **3,159 passed, 0 failed** |
| oracle comparison | 3,042 passed |
| audit edge cases | 47 passed (8 were RED before fixes) |
| audit resilience (crash/retry, concurrency, ordering) | 11 passed (all GREEN on the existing design) |
| original unit / golden / moto / rules | 33 / 11 / 12 / 3 passed |
| `terraform fmt -check`, `validate` (main + bootstrap) | pass |
| `terraform plan` (live, read-only) | 4 Lambda code updates, 0 add/destroy, no drift |
| live API matrix | 14/14 expected codes; 0 PII in responses |
| live results vs oracle | 0 mismatches (15 lessons, 4 students) |
| live log audit | 100 % JSON app lines; 0 PII hits in 5 log groups |
| live DLQ → alarm → replay | worked (after fixing the replay target) |

Details and raw output: [`TEST_RESULTS.md`](TEST_RESULTS.md).

## Remaining risks

1. The branch is not deployed. Live still has the broken raw-file link (P2-1) and the older validation.
2. Nobody receives alarms (P2-3).
3. Destroy and redeploy from zero was not re-run during the audit (destructive). The initial deploy was from zero.
4. Quarantine and conflict handling are verified on moto only, not on live S3 (needs an upload of a bad file).
5. Memory behaviour for very large files (the 50 MB cap) is not load-tested.

## Unverified

- `make destroy` (not run: destructive, needs authorization).
- Live quarantine path, live CONFLICTING_LESSON_ID, live rules-v2 switch (each needs a live upload or a data change).
- Behaviour when DynamoDB is unavailable (reasoned: exceptions → SQS retry → DLQ; moto crash tests cover the code path).

## Recommended next steps

1. Add the candidate's full name to the README (blocker).
2. Review this branch, merge to `main` → CI deploys the fixes. Then re-run `make results` and the live checks.
3. Set `alarm_email` and confirm the subscription.
4. Optional, with authorization: live quarantine test, live rules-v2 demo, Elixir bonus.
5. Before submitting: zip with `.git`, email with the API URL and key. Keep it running 72 h, then `make destroy`.
