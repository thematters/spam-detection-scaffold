#!/usr/bin/env python3
"""SQS→S3 worker for spam training-sample capture (軸二 L2 consumer).

Consumes `SpamSampleCaptured` messages emitted by matters-server's
enqueueSpamSample and appends them, batched per invocation, to the S3 training
bucket as date-partitioned JSONL. Messages are ALREADY de-identified at the
producer (comment/author ids are HMAC hashes); this worker performs no further
PII handling — it only persists.

Deploy as a Lambda with an SQS trigger on the spam-sample queue. IAM: s3:PutObject
on the training bucket. Env:
  EXPORT_S3_BUCKET   target bucket (e.g. matters-spam-training-samples)
  EXPORT_S3_PREFIX   prefix (default comment-training-samples/l2-captured)

Message shape (contract with matters-server src/common/notifications/spamSample.ts):
  {label, text, labelSource, score, commentHash, authorHash, occurredAt}

Idempotency: the S3 key includes the SQS messageId, so SQS at-least-once
redelivery overwrites the same object rather than duplicating rows.
"""
from __future__ import annotations

import json
import os

import boto3

_s3 = boto3.client("s3")

_REQUIRED = {"label", "text", "labelSource", "commentHash", "occurredAt"}


def _valid(rec: dict) -> bool:
    return _REQUIRED.issubset(rec) and bool(str(rec.get("text", "")).strip())


def handler(event, _context=None):
    bucket = os.environ["EXPORT_S3_BUCKET"]
    prefix = os.environ.get("EXPORT_S3_PREFIX", "comment-training-samples/l2-captured")

    records = event.get("Records", [])
    written = 0
    for r in records:
        message_id = r.get("messageId", "unknown")
        try:
            rec = json.loads(r["body"])
        except (KeyError, json.JSONDecodeError):
            print(f"skip unparseable message {message_id}")
            continue
        if not _valid(rec):
            print(f"skip invalid message {message_id}")
            continue

        occurred = str(rec["occurredAt"])
        day = occurred[:10].replace("-", "/")  # YYYY/MM/DD from ISO-8601
        key = f"{prefix}/dt={day}/{message_id}.json"
        _s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=(json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        written += 1

    print(f"persisted {written}/{len(records)} samples to s3://{bucket}/{prefix}")
    return {"written": written, "received": len(records)}
