#!/usr/bin/env bash
# Uploads every lesson file at the same time, in random order.
# Used to show that results do not depend on arrival order.
# Usage: ./scripts/upload_burst.sh <bucket-name> [prefix]
set -euo pipefail
BUCKET="${1:?usage: upload_burst.sh <bucket-name> [prefix]}"
PREFIX="${2:-incoming}"
cd "$(dirname "$0")/.."
grep -v '^$' scripts/upload_order.txt | sort -R | while read -r f; do
  key="${PREFIX}/${f#sample_data/lessons/}"
  echo "uploading $f -> s3://${BUCKET}/${key}"
  aws s3 cp "$f" "s3://${BUCKET}/${key}" --only-show-errors &
done
wait
echo "done"
