#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash gcp/run-test.sh PROJECT_ID" >&2
  exit 1
fi

PROJECT_ID="$1"
REGION="${REGION:-asia-northeast3}"
JOB_NAME="${JOB_NAME:-naver-crawler}"

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud CLI가 필요합니다. Google Cloud Shell에서 실행하세요." >&2
  exit 1
}

gcloud config set project "$PROJECT_ID"

echo "Cloud Run Job 수동 실행: ${JOB_NAME}"
gcloud run jobs execute "$JOB_NAME" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --wait
