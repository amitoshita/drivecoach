"""Delete all processed data so the sample files can be uploaded again from scratch.

Keeps: rules, students INITIAL records (both managed by Terraform).
Deletes: lessons, evaluations, students STATE/LESSONS records, every version
of every object under incoming/ and quarantine/.

Demo/test tool only. Usage: python scripts/reset_data.py <raw-bucket> --yes
"""
import sys

import boto3

PREFIX = "drivecoach"


def wipe_table(table, keep=lambda item: False):
    key_names = [k["AttributeName"] for k in table.key_schema]
    deleted = 0
    scan = {"ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(key_names))),
            "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(key_names)}}
    with table.batch_writer() as batch:
        while True:
            page = table.scan(**scan)
            for item in page["Items"]:
                if not keep(item):
                    batch.delete_item(Key={k: item[k] for k in key_names})
                    deleted += 1
            if "LastEvaluatedKey" not in page:
                break
            scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    print(f"{table.name}: deleted {deleted}")


def wipe_prefix(bucket, prefix):
    s3 = boto3.client("s3")
    deleted = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                   for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
    print(f"s3://{bucket}/{prefix}: deleted {deleted} object versions")


def main():
    if len(sys.argv) != 3 or sys.argv[2] != "--yes":
        sys.exit("usage: reset_data.py <raw-bucket> --yes")
    bucket = sys.argv[1]
    ddb = boto3.resource("dynamodb")
    wipe_table(ddb.Table(f"{PREFIX}-lessons"))
    wipe_table(ddb.Table(f"{PREFIX}-evaluations"))
    wipe_table(ddb.Table(f"{PREFIX}-students"), keep=lambda item: item["record"] == "INITIAL")
    wipe_prefix(bucket, "incoming/")
    wipe_prefix(bucket, "quarantine/")


if __name__ == "__main__":
    main()
