"""Ingest: S3 ObjectCreated -> SQS -> this Lambda.

parse + validate -> compute metrics -> store lesson once -> ask the evaluator
to rebuild the student's state (FIFO queue, one message group per student).

Failure policy:
  * the file itself is bad (LessonFileError) -> copy to quarantine/, metric, done.
    Retrying would never help.
  * anything else (throttling, network) -> report the message as failed so SQS
    retries it; after maxReceiveCount it lands in the DLQ for replay.
"""
from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import unquote_plus

import boto3

from .. import lesson_file, log, metrics, store

MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(50 * 1024 * 1024)))
QUARANTINE_PREFIX = os.environ.get("QUARANTINE_PREFIX", "quarantine/")

_s3 = None
_sqs = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def handler(event, context):
    log.bind(request_id=getattr(context, "aws_request_id", None), function="ingest")
    failures = []
    for message in event.get("Records", []):
        try:
            for bucket, key, sequencer in s3_objects(json.loads(message["body"])):
                process_object(bucket, key, sequencer)
        except Exception as exc:  # transient: let SQS retry, then DLQ
            log.error("ingest.failed", message_id=message["messageId"], error=type(exc).__name__,
                      detail=str(exc)[:300])
            log.metric("ProcessingFailures")
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def s3_objects(body: dict):
    """Yield (bucket, key, sequencer) from an S3 event notification (skips s3:TestEvent)."""
    for record in body.get("Records", []):
        if not record.get("eventName", "").startswith("ObjectCreated"):
            continue
        obj = record["s3"]["object"]
        yield record["s3"]["bucket"]["name"], unquote_plus(obj["key"]), obj.get("sequencer")


def process_object(bucket: str, key: str, sequencer: str = None) -> None:
    head = s3().head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] > MAX_FILE_BYTES:
        quarantine(bucket, key, "FILE_TOO_LARGE")
        return
    # Read exactly the version we sized, so version id, bytes and fingerprint agree.
    version = {"VersionId": head["VersionId"]} if head.get("VersionId") else {}
    body = s3().get_object(Bucket=bucket, Key=key, **version)["Body"].read()

    try:
        lesson = lesson_file.parse(body)
    except lesson_file.LessonFileError as exc:
        quarantine(bucket, key, exc.code, exc.detail)
        return

    lesson_metrics, events = metrics.compute(lesson)
    h = lesson.header
    item = {
        "lesson_id": lesson.lesson_id,
        "student_id": lesson.student_id,
        "instructor_id": h["instructor_id"],
        "car_id": h["car_id"],
        "lesson_type": lesson.lesson_type,
        "started_at": lesson.started_at,
        "uploaded_at": h.get("uploaded_at"),
        "firmware": h.get("firmware"),
        "speed_unit": h["speed_unit"],
        "sample_rate_hz": lesson.sample_rate_hz,
        "n_samples": lesson.n_samples,
        "fingerprint": lesson.fingerprint,
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_version_id": head.get("VersionId"),
        "metrics": lesson_metrics,
        "events": events,
        "ingested_at": store.now_iso(),
    }
    ids = dict(lesson_id=lesson.lesson_id, student_id=lesson.student_id, s3_key=key)

    try:
        store.put_lesson_once(item)
        log.info("lesson.stored", **ids, lesson_score=lesson_metrics["lesson_score"])
        log.metric("LessonsIngested")
    except store.DuplicateLesson as dup:
        if dup.existing["fingerprint"] != lesson.fingerprint:
            # Same lesson_id, different recording: never silently pick one.
            quarantine(bucket, key, "CONFLICTING_LESSON_ID", f"lesson {lesson.lesson_id} already stored")
            return
        if dup.existing["s3_key"] != key:
            store.record_duplicate_upload(lesson.lesson_id, key, h.get("uploaded_at"))
            log.info("lesson.duplicate_upload", **ids, original_key=dup.existing["s3_key"])
            log.metric("DuplicateUploads")
        # Fall through: re-request evaluation. It is idempotent, and it covers
        # a previous attempt that stored the lesson but crashed before enqueuing.

    store.index_lesson(lesson.student_id, lesson.lesson_id)
    # Deduplicate per upload event, not per lesson content: S3 redelivering the same
    # event is dropped, but re-processing the same lesson later (replay, re-upload)
    # must never be swallowed by the FIFO queue's 5-minute deduplication window.
    upload_event = f"{bucket}/{key}#{sequencer or head.get('VersionId') or head.get('ETag')}"
    request_evaluation(lesson.student_id, lesson.lesson_id, upload_event)


def request_evaluation(student_id: str, lesson_id: str, upload_event: str) -> None:
    sqs().send_message(
        QueueUrl=os.environ["EVALUATE_QUEUE_URL"],
        MessageBody=json.dumps({"student_id": student_id, "lesson_id": lesson_id}),
        MessageGroupId=student_id,
        MessageDeduplicationId=hashlib.sha256(upload_event.encode()).hexdigest(),
    )


def quarantine(bucket: str, key: str, code: str, detail: str = "") -> None:
    """Keep the original in place (raw archive) and copy it under quarantine/ with the reason."""
    target = QUARANTINE_PREFIX + key
    s3().copy_object(Bucket=bucket, Key=target, CopySource={"Bucket": bucket, "Key": key},
                     MetadataDirective="REPLACE", Metadata={"reason": code},
                     ServerSideEncryption="aws:kms", SSEKMSKeyId=os.environ["KMS_KEY_ARN"])
    log.warning("lesson.rejected", s3_key=key, reason=code, detail=detail[:200])
    log.metric("RejectedFiles", reason=code)
