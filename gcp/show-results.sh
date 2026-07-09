#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash gcp/show-results.sh PROJECT_ID" >&2
  exit 1
fi

PROJECT_ID="$1"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-crawler-data}"

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud CLI가 필요합니다. Google Cloud Shell에서 실행하세요." >&2
  exit 1
}

gcloud config set project "$PROJECT_ID"

echo "CSV 파일 목록: gs://${BUCKET_NAME}"
gcloud storage ls --recursive "gs://${BUCKET_NAME}/**.csv" --project "$PROJECT_ID" || true
