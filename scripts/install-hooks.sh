#!/usr/bin/env bash
# Point this clone at the tracked hooks. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh 2>/dev/null || true

echo "hooks installed (core.hooksPath=.githooks)"
echo "pre-commit runs scripts/verify.sh --fast"
git config --get core.hooksPath
