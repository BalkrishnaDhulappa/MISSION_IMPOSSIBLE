#!/usr/bin/env bash
# MTF nursery cron wrapper — flock, env, logging (fire_shop style).
set -euo pipefail

JOB="${1:?usage: mtf_cron.sh <job>}"
shift

MTF_ROOT="${MTF_ROOT:-/home/ubuntu/MISSION_IMPOSSIBLE/mtf_nursery}"
ENV_FILE="${MTF_ENV_FILE:-/home/ubuntu/.env_fire_shop}"
TOKEN_PATH="${KITE_TOKEN_PATH:-/home/ubuntu/fire_shop/.kite_token}"
PYTHON="${MTF_PYTHON:-$MTF_ROOT/.venv/bin/python3}"
LOG_DIR="${MTF_LOG_DIR:-$MTF_ROOT/logs}"
LOCK="/tmp/mtf_${JOB}.lock"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${JOB}.log"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export KITE_TOKEN_PATH="$TOKEN_PATH"
export MTF_ENV_FILE="$ENV_FILE"

run_job() {
  cd "$MTF_ROOT"
  "$PYTHON" "$@" >>"$LOG" 2>&1
}

case "$JOB" in
  emi_funding)
    run_job jobs/run_emi_funding.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  liquid_funding)
    run_job jobs/run_liquid_funding.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  rms_guard)
    run_job jobs/run_rms_guard.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  scan)
    run_job jobs/run_scan.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  sell)
    run_job jobs/run_sell.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  buy)
    run_job jobs/run_buy.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  status)
    run_job jobs/run_status_day.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  car_check)
    run_job jobs/run_car_check.py --env-file "$ENV_FILE"
    ;;
  dry_run)
    run_job jobs/run_dry_run.py --env-file "$ENV_FILE" --token-path "$TOKEN_PATH"
    ;;
  *)
    echo "Unknown job: $JOB" >&2
    exit 1
    ;;
esac
