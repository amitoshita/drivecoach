"""Phase 6 of the self-audit: idempotency, concurrency, ordering and partial failure,
run through the real handlers on mocked AWS."""
import json

import pytest
from botocore.exceptions import ClientError

from tests.conftest import ROOT
from tests.test_handlers_aws import BUCKET, Ctx, api_get, aws, drain, final_states, upload  # noqa: F401

EXPECTED = {
    "S-001": ("STANDARD", 12, 0, "COMPLETED", 89.73),
    "S-002": ("STANDARD", 5, 2, "ACTIVE", None),
    "S-003": ("EXTENDED", 7, 0, "ACTIVE", None),
    "S-004": ("EXTENDED", 10, 2, "INSTRUCTOR_REVIEW", None),
}


def check_final(aws):
    for sid, s in final_states(aws).items():
        assert (s["program"], s["lessons_completed"], s["review_count"], s["status"], s["final_score"]) \
            == EXPECTED[sid], sid


def event_for(key, sequencer="0001"):
    body = {"Records": [{"eventName": "ObjectCreated:Put",
                         "s3": {"bucket": {"name": BUCKET}, "object": {"key": key, "sequencer": sequencer}}}]}
    return {"Records": [{"messageId": f"m-{sequencer}", "body": json.dumps(body)}]}


def audit_count(aws, lesson_id):
    return len(api_get(aws, "lessons", lesson_id)[1]["audit_trail"])


# ---------- ordering ----------

def test_reverse_upload_order(aws, upload_files):
    for path in reversed(upload_files):
        upload(aws, path)
        drain(aws)
    check_final(aws)


def test_retry_after_later_lessons_completed(aws, upload_files):
    """Everything processed; then the oldest S-002 file is redelivered. Nothing changes."""
    for path in upload_files:
        upload(aws, path)
    drain(aws)
    before = {lid: audit_count(aws, lid) for lid in ("L-1004", "L-1005", "L-1007", "L-1008")}
    upload(aws, next(p for p in upload_files if p.name == "L-1004.csv"))
    drain(aws)
    check_final(aws)
    assert {lid: audit_count(aws, lid) for lid in before} == before


# ---------- idempotency ----------

def test_same_event_three_times(aws, upload_files):
    path = next(p for p in upload_files if p.name == "L-1005.csv")
    key = "incoming/" + str(path.relative_to(ROOT / "sample_data" / "lessons"))
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
    for _ in range(3):
        assert aws["ingest"].handler(event_for(key, "00C1"), Ctx()) == {"batchItemFailures": []}
        drain(aws)
    state = api_get(aws, "students", "S-002")[1]["state"]
    assert state["review_count"] == 1 and audit_count(aws, "L-1005") == 1


def test_key_with_spaces_and_plus(aws, upload_files):
    path = next(p for p in upload_files if p.name == "L-1001.csv")
    key = "incoming/CAR 01/2026-10-05/L-1001 (copy)+v2.csv"
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
    encoded = key.replace("+", "%2B").replace(" ", "+")          # how S3 encodes keys in events
    out = aws["ingest"].handler(event_for(encoded), Ctx())
    assert out == {"batchItemFailures": []}
    drain(aws)
    assert api_get(aws, "lessons", "L-1001")[1]["evaluation"]["result"] == "PASS"


# ---------- partial failure (crash between steps) ----------

class Boom(Exception):
    pass


def fail_once(monkeypatch, module, name):
    real = getattr(module, name)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Boom(name)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, flaky)


@pytest.mark.parametrize("step", ["index_lesson", "put_lesson_once"])
def test_ingest_crash_then_retry(aws, upload_files, monkeypatch, step):
    path = next(p for p in upload_files if p.name == "L-1001.csv")
    key = "incoming/" + str(path.relative_to(ROOT / "sample_data" / "lessons"))
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
    fail_once(monkeypatch, aws["store"], step)
    assert aws["ingest"].handler(event_for(key, "00D1"), Ctx())["batchItemFailures"]   # crashed
    assert aws["ingest"].handler(event_for(key, "00D1"), Ctx()) == {"batchItemFailures": []}  # SQS retry
    drain(aws)
    assert api_get(aws, "students", "S-001")[1]["state"]["lessons_completed"] == 10
    assert audit_count(aws, "L-1001") == 1


@pytest.mark.parametrize("step", ["append_evaluation", "save_state", "get_lessons"])
def test_evaluate_crash_then_retry(aws, upload_files, monkeypatch, step):
    for name in ("L-1001.csv", "L-1002.csv"):
        upload(aws, next(p for p in upload_files if p.name == name))
    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)["Messages"]
    records = [{"messageId": m["MessageId"], "body": m["Body"]} for m in msgs]
    fail_once(monkeypatch, aws["store"], step)
    first = aws["evaluate"].handler({"Records": records}, Ctx())
    assert first["batchItemFailures"]                                 # crash reported
    retry = [r for r in records if r["messageId"] in {f["itemIdentifier"] for f in first["batchItemFailures"]}]
    assert aws["evaluate"].handler({"Records": retry}, Ctx()) == {"batchItemFailures": []}
    state = api_get(aws, "students", "S-001")[1]["state"]
    assert state["lessons_completed"] == 11
    assert audit_count(aws, "L-1001") == 1 and audit_count(aws, "L-1002") == 1


# ---------- concurrency ----------

def test_concurrent_writer_is_detected_by_optimistic_lock(aws, upload_files, monkeypatch):
    """Simulate a second evaluator saving S-001's state between our read and our write."""
    upload(aws, next(p for p in upload_files if p.name == "L-1001.csv"))
    drain(aws)                                                      # state_version = 1
    store = aws["store"]
    real_get = store.get_student

    def stale_get(student_id):
        snapshot = real_get(student_id)
        store.save_state(student_id, snapshot["state"], snapshot["state_version"])  # someone else wins
        return snapshot                                              # we still hold version 1
    monkeypatch.setattr(store, "get_student", stale_get)
    upload(aws, next(p for p in upload_files if p.name == "L-1002.csv"))
    with pytest.raises(ClientError) as err:
        aws["evaluate"].rebuild_student("S-001", trigger="test")
    assert err.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    monkeypatch.setattr(store, "get_student", real_get)
    aws["evaluate"].rebuild_student("S-001", trigger="retry")         # what the SQS retry does
    assert api_get(aws, "students", "S-001")[1]["state"]["lessons_completed"] == 11


def test_audit_history_cannot_be_overwritten(aws, upload_files):
    upload(aws, upload_files[0])
    drain(aws)
    record = api_get(aws, "lessons", "L-1001")[1]["audit_trail"][0]
    table = aws["res"].Table("dc-evaluations")
    with pytest.raises(ClientError):
        table.put_item(Item={"lesson_id": "L-1001", "evaluated_at": record["evaluated_at"], "result": "PASS"},
                       ConditionExpression="attribute_not_exists(evaluated_at)")
