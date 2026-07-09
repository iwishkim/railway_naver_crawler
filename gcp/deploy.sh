#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash gcp/deploy.sh PROJECT_ID" >&2
  exit 1
fi

PROJECT_ID="$1"
REGION="${REGION:-asia-northeast3}"
JOB_NAME="${JOB_NAME:-naver-crawler}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-crawler-data}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-naver-crawler-sa}"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-naver-crawler}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/${JOB_NAME}:latest"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud CLI가 필요합니다. Google Cloud Shell에서 실행하세요." >&2
  exit 1
}

echo "프로젝트 설정: ${PROJECT_ID}"
gcloud config set project "$PROJECT_ID"

echo "필요한 Google Cloud API 활성화"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  --project "$PROJECT_ID"

echo "Cloud Storage 버킷 확인: gs://${BUCKET_NAME}"
if gcloud storage buckets describe "gs://${BUCKET_NAME}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "버킷이 이미 있습니다."
else
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --uniform-bucket-level-access
fi

echo "서비스 계정 확인: ${SERVICE_ACCOUNT_EMAIL}"
if gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "서비스 계정이 이미 있습니다."
else
  gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
    --project "$PROJECT_ID" \
    --display-name "Naver crawler Cloud Run Job"
fi

echo "버킷 쓰기 권한 부여"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role "roles/storage.objectUser" \
  --project "$PROJECT_ID" >/dev/null

echo "Artifact Registry 저장소 확인: ${ARTIFACT_REPOSITORY}"
if gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "Artifact Registry 저장소가 이미 있습니다."
else
  gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
    --repository-format "docker" \
    --location "$REGION" \
    --description "Docker images for Naver crawler" \
    --project "$PROJECT_ID"
fi

echo "Dockerfile로 컨테이너 이미지 빌드 및 업로드: ${IMAGE}"
gcloud builds submit . \
  --tag "$IMAGE" \
  --project "$PROJECT_ID"

echo "Cloud Run Job 배포 또는 업데이트: ${JOB_NAME}"
gcloud run jobs deploy "$JOB_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --service-account "$SERVICE_ACCOUNT_EMAIL" \
  --set-env-vars "TARGET=석촌동,REGIONS=석촌동,KEYWORD_LIMIT=3,MAX_WORKERS=1,MAX_SCROLL=5,HEADLESS=true,DATA_DIR=/data" \
  --cpu "1" \
  --memory "2Gi" \
  --task-timeout "30m" \
  --max-retries "0" \
  --clear-volumes \
  --add-volume="mount-path=/data,type=cloud-storage,bucket=${BUCKET_NAME},readonly=false"

echo "시험 실행 시작"
gcloud run jobs execute "$JOB_NAME" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --wait

echo "Cloud Storage 파일 목록"
gcloud storage ls --recursive "gs://${BUCKET_NAME}/**" --project "$PROJECT_ID" || true
