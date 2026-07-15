#!/usr/bin/env bash
# Cross-repo CI guard: fuzz-reachability and cov-analysis share a `key`
# normalization contract. Running both suites together makes a change to
# that contract in either repo fail loudly for the other.
#
#   scripts/ci_cross_repo.sh
#   COV_ANALYSIS_DIR=/path/to/cov-analysis scripts/ci_cross_repo.sh
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cov="${COV_ANALYSIS_DIR:-$root/../cov-analysis}"

PATH="$(go env GOPATH 2>/dev/null)/bin:$PATH" make -C "$root" test

if [ "${SKIP_CROSS_REPO:-0}" = 1 ]; then
  echo "SKIP cross-repo: explicitly disabled with SKIP_CROSS_REPO=1"
elif [ -d "$cov" ]; then
  PATH=/usr/bin:$PATH bash "$cov/tests/run.sh"
else
  echo "error: cov-analysis not found at $cov (set COV_ANALYSIS_DIR or SKIP_CROSS_REPO=1)" >&2
  exit 1
fi
