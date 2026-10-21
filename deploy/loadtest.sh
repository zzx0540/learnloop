#!/usr/bin/env bash
set -Eeuo pipefail

: "${COOKIE:?请设置 COOKIE，例如 ll_session=...}"
: "${COURSE_ID:?请设置 COURSE_ID}"

BASE_URL="${BASE_URL:-https://learn.example.com/api/v1}"
REQUESTS="${REQUESTS:-200}"
CONCURRENCY="${CONCURRENCY:-5}"
RESULT_FILE="$(mktemp /opt/learnloop/.loadtest.XXXXXX)"
trap 'rm -f "$RESULT_FILE"' EXIT

run_endpoint() {
  local name="$1"
  local path="$2"
  : >"$RESULT_FILE"
  export BASE_URL COOKIE path
  seq "$REQUESTS" | xargs -P "$CONCURRENCY" -n 1 sh -c \
    'curl -sS -o /dev/null -w "%{http_code} %{time_total}\n" -b "$COOKIE" "$BASE_URL$path"' \
    >>"$RESULT_FILE"
  local failures
  failures="$(awk '$1 != 200 {count++} END {print count+0}' "$RESULT_FILE")"
  if [[ "$failures" -ne 0 ]]; then
    echo "$name 出现 $failures 个非 200 响应" >&2
    return 1
  fi
  local p95
  p95="$(awk '{print $2}' "$RESULT_FILE" | sort -n | awk -v n="$REQUESTS" \
    'NR == int((n * 95 + 99) / 100) {printf "%.1f", $1 * 1000}')"
  echo "$name p95=${p95}ms requests=$REQUESTS concurrency=$CONCURRENCY"
}

run_endpoint overview "/courses/$COURSE_ID/overview"
run_endpoint plan "/courses/$COURSE_ID/plan"
run_endpoint reviews-queue "/courses/$COURSE_ID/reviews/queue"
