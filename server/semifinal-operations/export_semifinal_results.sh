#!/usr/bin/env bash
set -euo pipefail
umask 077
if (( $# > 1 )); then echo '用法：export_semifinal_results.sh [--provisional]' >&2; exit 2; fi
case "${1:-}" in
  '') ranking_flags=(--require-complete) ;;
  --provisional) ranking_flags=() ;;
  *) echo '用法：export_semifinal_results.sh [--provisional]' >&2; exit 2 ;;
esac
ranking_archive=/var/lib/aiops-contest/semifinal-results/20261022
install -d -m 0700 "$ranking_archive"
ranking_output="$ranking_archive/results-$(date -u +%Y%m%dT%H%M%S)-$$"
exec python3 /opt/aiops-competition-submissions/tools/semifinal_results.py \
  --plan /root/semifinal-20261022-preparation/semifinal-20261022-plan.json \
  --output "$ranking_output" "${ranking_flags[@]}"
