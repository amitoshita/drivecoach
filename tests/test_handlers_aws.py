"""Run the real Lambda handlers against mocked AWS (moto).

The tables mirror infra/dynamodb.tf. This exercises idempotency, quarantine,
late/out-of-order arrival, audit history and the read API end to end.
"""
import csv
import importlib
import json
import random

import boto3
import pytest
from moto import mock_aws

from tests.conftest import ROOT

REGION = "us-east-1"
BUCKET = "dc-raw-test"
NON_PII = ("program", "lessons_completed", "review_count", "next_lesson_type", "status")


class Ctx:
    aws_request_id = "test-request"


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
        for name in ("LESSONS", "EVALUATIONS", "STUDENTS", "RULES"):
            monkeypatch.setenv(f"{name}_TABLE", f"dc-{name.lower()}")
        ddb = boto3.client("dynamodb", region_name=REGION)

        def table(name, keys, gsi=None):
            attrs = {k for k, _ in keys} | ({k for k, _ in gsi} if gsi else set())
            args = dict(TableName=name, BillingMode="PAY_PER_REQUEST",
                        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
                        KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in keys])
            if gsi:
                args["GlobalSecondaryIndexes"] = [{
                    "IndexName": "by_student", "Projection": {"ProjectionType": "ALL"},
                    "KeySchema": [{"AttributeName": k, "KeyType": t} for k, t in gsi]}]
            ddb.create_table(**args)

        table("dc-lessons", [("lesson_id", "HASH")], [("student_id", "HASH"), ("started_at", "RANGE")])
        table("dc-evaluations", [("lesson_id", "HASH"), ("evaluated_at", "RANGE")],
              [("student_id", "HASH"), ("evaluated_at", "RANGE")])
        table("dc-students", [("student_id", "HASH"), ("record", "RANGE")])
        table("dc-rules", [("version", "HASH")])

        res = boto3.resource("dynamodb", region_name=REGION)
        rules_doc = (ROOT / "rules" / "rules_v1.json").read_text()
        res.Table("dc-rules").put_item(Item={"version": "v1", "document": rules_doc})
        with open(ROOT / "sample_data" / "students.csv", newline="") as f:
            for row in csv.DictReader(f):
                state = {k: row[k] for k in NON_PII}
                state.update(lessons_completed=int(row["lessons_completed"]),
                             review_count=int(row["review_count"]), final_score=None,
                             passed_regular_count=0, passed_regular_score_sum=0)
                res.Table("dc-students").put_item(Item={"student_id": row["student_id"],
                                                        "record": "INITIAL", "state": state})

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        key_arn = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["Arn"]
        monkeypatch.setenv("KMS_KEY_ARN", key_arn)
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="dc-evaluate.fifo",
                                     Attributes={"FifoQueue": "true"})["QueueUrl"]
        monkeypatch.setenv("EVALUATE_QUEUE_URL", queue_url)

        from drivecoach import store
        from drivecoach.handlers import api, evaluate, ingest
        for mod in (store, ingest, api, evaluate):
            importlib.reload(mod)
        yield {"s3": s3, "sqs": sqs, "queue_url": queue_url, "res": res,
               "ingest": ingest, "evaluate": evaluate, "api": api, "store": store}


_sequencer = iter(range(1, 10**6))


def upload(aws, path, key=None, sequencer=None):
    key = key or "incoming/" + str(path.relative_to(ROOT / "sample_data" / "lessons"))
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
    sequencer = sequencer or f"{next(_sequencer):016X}"
    s3_event = {"Records": [{"eventName": "ObjectCreated:Put",
                             "s3": {"bucket": {"name": BUCKET},
                                    "object": {"key": key, "sequencer": sequencer}}}]}
    out = aws["ingest"].handler({"Records": [{"messageId": key, "body": json.dumps(s3_event)}]}, Ctx())
    assert out == {"batchItemFailures": []}
    return key


def drain(aws):
    while True:
        msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10).get("Messages", [])
        if not msgs:
            return
        records = [{"messageId": m["MessageId"], "body": m["Body"]} for m in msgs]
        assert aws["evaluate"].handler({"Records": records}, Ctx()) == {"batchItemFailures": []}
        for m in msgs:
            aws["sqs"].delete_message(QueueUrl=aws["queue_url"], ReceiptHandle=m["ReceiptHandle"])


def api_get(aws, path_kind, item_id):
    route = f"GET /{path_kind}/{{id}}"
    out = aws["api"].handler({"routeKey": route, "pathParameters": {"id": item_id}}, Ctx())
    return out["statusCode"], json.loads(out["body"])


def final_states(aws):
    return {sid: api_get(aws, "students", sid)[1]["state"] for sid in ("S-001", "S-002", "S-003", "S-004")}


def test_field_order_end_to_end(aws, upload_files):
    for path in upload_files:
        upload(aws, path)
        drain(aws)

    states = final_states(aws)
    assert states["S-001"]["status"] == "COMPLETED" and states["S-001"]["final_score"] == 89.73
    assert states["S-002"]["program"] == "STANDARD" and states["S-002"]["review_count"] == 2
    assert states["S-003"]["program"] == "EXTENDED" and states["S-003"]["lessons_completed"] == 7
    assert states["S-004"]["status"] == "INSTRUCTOR_REVIEW"

    status, lesson = api_get(aws, "lessons", "L-1007")
    assert status == 200
    assert lesson["evaluation"]["result"] == "REVIEW"
    assert len(lesson["audit_trail"]) == 1                      # retry did not re-evaluate
    assert lesson["duplicate_uploads"][0]["s3_key"].endswith("L-1007_retry1.csv")
    assert lesson["raw_file_url"].startswith("https://")
    assert "full_name" not in json.dumps(lesson) and "national_id" not in json.dumps(lesson)


def test_all_at_once_shuffled(aws, upload_files):
    files = list(upload_files)
    random.Random(7).shuffle(files)
    for path in files:
        upload(aws, path)
    drain(aws)
    states = final_states(aws)
    assert states["S-001"]["final_score"] == 89.73
    assert states["S-002"]["program"] == "STANDARD"
    assert states["S-003"]["program"] == "EXTENDED"
    assert states["S-004"]["status"] == "INSTRUCTOR_REVIEW"


def test_late_file_reevaluates_and_keeps_history(aws, upload_files):
    late = next(p for p in upload_files if p.name == "L-1005.csv")   # S-002's first REVIEW
    for path in upload_files:
        if path != late:
            upload(aws, path)
    drain(aws)
    # Without L-1005, L-1007 is S-002's first review.
    assert api_get(aws, "students", "S-002")[1]["state"]["review_count"] == 1

    upload(aws, late)
    drain(aws)
    state = api_get(aws, "students", "S-002")[1]["state"]
    assert state["review_count"] == 2 and state["lessons_completed"] == 5
    trail = api_get(aws, "lessons", "L-1007")[1]["audit_trail"]
    assert len(trail) == 2                                    # original + re-evaluation
    assert trail[1]["supersedes"] == trail[0]["evaluation_id"]
    assert trail[1]["trigger"] == "L-1005"


def test_redelivery_is_idempotent(aws, upload_files):
    path = upload_files[0]
    upload(aws, path, sequencer="00A1")
    drain(aws)
    upload(aws, path, sequencer="00A1")   # S3/SQS at-least-once: same event delivered twice
    drain(aws)
    trail = api_get(aws, "lessons", "L-1001")[1]["audit_trail"]
    assert len(trail) == 1
    assert api_get(aws, "lessons", "L-1001")[1]["duplicate_uploads"] == []


def test_bad_file_is_quarantined(aws, tmp_path):
    bad = tmp_path / "broken.csv"
    bad.write_bytes(b"# lesson_id: L-X\nnot a lesson\n")
    key = "incoming/CAR-09/2026-10-09/broken.csv"
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=bad.read_bytes())
    event = {"Records": [{"eventName": "ObjectCreated:Put",
                          "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}
    out = aws["ingest"].handler({"Records": [{"messageId": "m1", "body": json.dumps(event)}]}, Ctx())
    assert out == {"batchItemFailures": []}
    head = aws["s3"].head_object(Bucket=BUCKET, Key="quarantine/" + key)
    assert head["Metadata"]["reason"] == "MISSING_HEADER"


def test_conflicting_lesson_id_is_quarantined(aws, upload_files):
    original = next(p for p in upload_files if p.name == "L-1001.csv")
    upload(aws, original)
    tampered = original.read_bytes().replace(b"0,0.12,", b"0,0.13,", 1)
    key = "incoming/CAR-01/2026-10-05/L-1001_other.csv"
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=tampered)
    event = {"Records": [{"eventName": "ObjectCreated:Put",
                          "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}
    aws["ingest"].handler({"Records": [{"messageId": "m1", "body": json.dumps(event)}]}, Ctx())
    head = aws["s3"].head_object(Bucket=BUCKET, Key="quarantine/" + key)
    assert head["Metadata"]["reason"] == "CONFLICTING_LESSON_ID"


def test_transient_error_reports_batch_failure(aws, monkeypatch):
    event = {"Records": [{"eventName": "ObjectCreated:Put",
                          "s3": {"bucket": {"name": BUCKET}, "object": {"key": "incoming/missing.csv"}}}]}
    out = aws["ingest"].handler({"Records": [{"messageId": "m1", "body": json.dumps(event)}]}, Ctx())
    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}


def test_api_rejects_bad_ids_and_unknown(aws):
    assert api_get(aws, "students", "../etc")[0] == 400
    assert api_get(aws, "students", "S-999")[0] == 404
    assert api_get(aws, "lessons", "L-999")[0] == 404


def test_authorizer(aws, monkeypatch):
    boto3.client("ssm", region_name=REGION).put_parameter(
        Name="/dc/key", Value="s3cret", Type="SecureString")
    monkeypatch.setenv("API_KEY_PARAM", "/dc/key")
    from drivecoach.handlers import authorizer
    importlib.reload(authorizer)
    assert authorizer.handler({"headers": {"X-Api-Key": "s3cret"}}, Ctx()) == {"isAuthorized": True}
    assert authorizer.handler({"headers": {"x-api-key": "nope"}}, Ctx()) == {"isAuthorized": False}
    assert authorizer.handler({"headers": {}}, Ctx()) == {"isAuthorized": False}


def test_lesson_of_unknown_student_is_kept_and_evaluated(aws, upload_files):
    source = next(p for p in upload_files if p.name == "L-1001.csv").read_bytes()
    other = source.replace(b"# lesson_id: L-1001", b"# lesson_id: L-9001").replace(
        b"# student_id: S-001", b"# student_id: S-404")
    key = "incoming/CAR-01/2026-10-05/L-9001.csv"
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=other)
    event = {"Records": [{"eventName": "ObjectCreated:Put",
                          "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}
    aws["ingest"].handler({"Records": [{"messageId": "m1", "body": json.dumps(event)}]}, Ctx())
    drain(aws)
    lesson = api_get(aws, "lessons", "L-9001")[1]
    assert lesson["evaluation"]["result"] == "PASS"
    assert "unknown student" in lesson["evaluation"]["notes"][0]
    assert api_get(aws, "students", "S-404")[0] == 404


def test_malformed_evaluate_message_does_not_fail_the_batch(aws, upload_files):
    upload(aws, upload_files[0])
    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)["Messages"]
    records = [{"messageId": "bad", "body": "{not json"}, {"messageId": "manual", "body": '{"reason": "x"}'}]
    records += [{"messageId": m["MessageId"], "body": m["Body"]} for m in msgs]
    assert aws["evaluate"].handler({"Records": records}, Ctx()) == {"batchItemFailures": []}
    assert api_get(aws, "lessons", "L-1001")[1]["evaluation"]["result"] == "PASS"


def test_fifo_dedup_is_per_upload_event_not_per_lesson(aws, upload_files):
    """Live bug: dedup ids built from lesson content made SQS drop a re-upload of the
    same recording within its 5-minute window, so the lesson was never evaluated.
    (moto forgets ids once a message is consumed, so both sends happen before draining.)"""
    path = next(p for p in upload_files if p.name == "L-1001.csv")

    def queued():
        attrs = aws["sqs"].get_queue_attributes(QueueUrl=aws["queue_url"],
                                                AttributeNames=["ApproximateNumberOfMessages"])
        return int(attrs["Attributes"]["ApproximateNumberOfMessages"])

    upload(aws, path, sequencer="00B1")
    upload(aws, path, sequencer="00B1")    # S3 redelivers the same event: deduplicated
    assert queued() == 1
    upload(aws, path, sequencer="00B2")    # a new upload event: must get through
    assert queued() == 2
