# Architecture review

## Current architecture

```
In-car box ─PUT─► S3 raw (private, SSE-KMS, versioned, TLS-only)
                    │ ObjectCreated (incoming/*.csv)
                    ▼
                 SQS ingest ──3 failures──► ingest DLQ ──make replay-dlq──┐
                    │ batch 5, partial-batch failures                      │
                    ▼                                                      │
                 λ ingest: parse + validate → metrics → put lesson once ◄──┘
                    │  (bad file → copy to quarantine/, metric, no retry)
                    │  ADD lesson_id to students[record=LESSONS]
                    ▼
                 SQS FIFO evaluate (MessageGroupId = student_id; dedup = S3 event)
                    │ ──5 failures──► evaluate DLQ (FIFO)
                    ▼
                 λ evaluate: read INITIAL + LESSONS index (consistent) → BatchGet lessons
                    → replay sorted by (started_at, lesson_id) with rules in force on each date
                    → append audit rows whose outcome is new/changed (supersedes links)
                    → save STATE (optimistic lock on state_version)

API Gateway HTTP API ─► λ authorizer (x-api-key vs SSM SecureString, cached 5 min)
                    └─► λ api (read only) ─► DynamoDB; presigned SigV4 link to the scored S3 version
CloudWatch: JSON logs (IDs only), EMF metrics, 10 alarms → SNS (KMS) ; dashboard
CI: GitHub Actions → OIDC → deploy role → terraform plan/apply → smoke test
```

Data model: `lessons` (pk lesson_id; GSI by_student), `evaluations` (pk lesson_id, sk evaluated_at;
GSI by_student), `students` (pk student_id, sk record ∈ INITIAL | STATE | LESSONS), `rules` (pk version).
All lookups are by key or partition. The only Scan is the `rules` table (a handful of items, cached for 60 s).

## Strengths

1. **State is a pure function** of (initial state, lessons, rules). Upload order, duplicates, retries and
   late files cannot produce a different answer. Verified live with a shuffled burst.
2. **Idempotency at every hop.** Conditional put on `lesson_id` + content fingerprint; idempotent set-ADD
   for the index; FIFO dedup per S3 event; audit rows written only when an outcome changes; optimistic
   lock on the state. A crash at any of five points recovers on retry (tested).
3. **The business logic has no AWS imports.** It is testable offline and was cross-checked by an independent oracle.
4. **Explainable decisions.** Every audit row has the rules version + sha256, the input metrics, the reasons
   (which condition, with which value), state before and after, the trigger, and a `supersedes` chain.
5. **Security by subtraction.** Personal data never enters AWS. No public data path. No long-lived CI keys.
6. **Cheap and serverless.** Nothing runs while idle. Measured ingest is 0.65 s per file.

## Weaknesses

| Weakness | Impact | Mitigation / proposal |
|---|---|---|
| Shared static API key | no per-instructor scoping, rotation is manual | Cognito + JWT authorizer, instructor_id claims |
| Full replay per message | O(lessons per student). Fine at ≤ 30, wasteful at scale | snapshot state per lesson, replay from the latest snapshot before the new `started_at` |
| Whole file in memory | 135 MB for 2 MB; a 50 MB file is untested | streaming parser, or a lower cap |
| No automatic rebuild on enrolment | a lesson before enrolment never counts | DynamoDB Stream on INITIAL items → evaluate queue |
| Alarms had no subscriber (fixed after the audit) | failures went unnoticed | `ALARM_EMAIL` repository variable, passed by CI |
| Deploy on every push to `main` | no review gate | branch protection + environment reviewers |
| moto ≠ AWS | two live-only bugs (FIFO dedup window, SigV2 on KMS) passed tests | keep a small live smoke suite in CI (upload one file, read it back, fetch the link) |

## AWS security assessment

| Control | State | Evidence |
|---|---|---|
| S3 private | ✅ | public access block ×4, `BucketOwnerEnforced`, anonymous GET 403 (live) |
| Encryption at rest | ✅ | CMK with rotation for S3, DynamoDB ×4, SSM, SNS; SQS SSE (messages hold keys only) |
| Encryption in transit | ✅ | bucket policy denies non-TLS and TLS < 1.2 |
| Least privilege | ✅ (one P3) | 4 roles, 4–14 explicit actions, ARN-scoped; ingest can update any `students` item |
| Secrets | ✅ | key generated (`random_password`) → SSM SecureString (CMK); never in git. The starter key is treated as leaked |
| PII | ✅ | only operational columns seeded; 0 hits in logs and API responses (live) |
| CI credentials | ✅ | OIDC, trust bound to the immutable repo id + `main` / `production` |
| Deploy role | ⚠️ | broad (`s3:*`, `lambda:*`, `iam:*Role*`…) but limited to `drivecoach-*` resources; inherent to a deployer |
| Terraform state | ⚠️ | contains the API key; the state bucket is private, KMS-encrypted, versioned, TLS-only |
| API | ✅ | authorizer with constant-time compare; throttling 10 rps / burst 20; access logs; strict id regex |

## Failure recovery analysis

| Failure | What happens | Recovery |
|---|---|---|
| Bad file | quarantined with reason; `RejectedFiles` alarm | fix the box, re-upload |
| Same lesson, different content | quarantined `CONFLICTING_LESSON_ID` | human decides |
| DynamoDB / S3 throttled or down | exception → batch item failure → SQS retry with visibility timeout (6× Lambda timeout) → DLQ after 3/5 | `ProcessingFailures` alarm immediately, DLQ alarm later; `make replay-dlq` (idempotent) |
| Crash after lesson stored, before indexed or enqueued | retry finds the duplicate (same key) → indexes + enqueues | automatic (tested) |
| Crash after audit rows, before state saved | retry: audit rows equal → skipped; state saved | automatic (tested) |
| Two evaluators on one student | FIFO prevents it; if it happened anyway, the optimistic lock fails one → retry | automatic (tested) |
| Malformed evaluate message | logged + metric, dropped (cannot ever succeed, would block its FIFO group) | investigate from the log |
| Bad rules published | `validate` raises in every evaluation → DLQ; the CI rules tests block it earlier | fix rules → replay |
| Bad students.csv | Terraform `precondition` fails the plan | fix the file |

## Ordering and consistency analysis

- **Requirement:** a student's lessons are applied in `started_at` order. **Guaranteed by construction:**
  every rebuild sorts all known lessons by `(started_at, lesson_id)` and folds from the initial state.
  FIFO is used for *mutual exclusion per student*, not for chronology. Arrival order is irrelevant.
- **Visibility:** the evaluator reads the lesson index and the lessons with strongly consistent reads, so a
  lesson acknowledged by ingest is always seen. An earlier GSI version could miss it.
- **Late files** re-judge later lessons; history is append-only, with `supersedes` + `trigger`.
  Consequence: a published decision can change. See the README for how to handle that in the product.
- **Ties** on `started_at` are broken by `lesson_id` (deterministic, tested).
- **Eventual consistency in reads:** `GET /students/{id}` lists lessons through a GSI, so it may lag by
  milliseconds. Acceptable for a read API.

## Scalability assessment (100× = 20,000 cars, 60k lessons/day, ~0.7 lessons/s average)

| Component | At 100× | Action |
|---|---|---|
| S3 + SQS ingest | trivial | raise `maximum_concurrency` (now 20) |
| λ ingest | ~11 GB-s/hour of CPU at 0.65 s/file | fine; a faster parser cuts cost ~10× |
| FIFO evaluate | one group per student; high-throughput FIFO handles thousands of msg/s across groups | fine |
| DynamoDB | keys spread by student / lesson; no hot partition | on-demand → provisioned + autoscaling when steady |
| Replay cost | grows with lessons per student | snapshots |
| Storage | ~3.8 TB/month raw | lifecycle → Glacier; keep downsampled data only |
| Observability | per-dimension metric cost grows with result types, not volume | keep |
| Analytics | do not scan DynamoDB | Streams → Firehose → S3 Parquet → Athena |

## Cost considerations

Estimate (README): ≈ $12/month in month 1, ≈ $15 by month 12 for 200 cars. **CloudWatch custom metrics
and the dashboard dominate** (~$9), not compute. Measured Lambda durations confirm the compute line
(costed at 1 s per file, measured 0.65 s). This is an estimate from list prices, not a bill. Actual spend
was not queried (Cost Explorer API calls are themselves billed). Check the budget page in the console.

## Suggested improvements (priority order)

1. ~~Deploy the audit fixes~~ and ~~set `alarm_email`~~ (done after the audit).
2. One live smoke test in CI that uploads a fixture lesson and reads it back.
3. Cognito/JWT authorizer with per-instructor access.
4. Enrolment-triggered rebuild (DynamoDB Stream).
5. Snapshots for replay; streaming parser.
