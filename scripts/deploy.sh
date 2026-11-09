#!/usr/bin/env bash
# Publish this checkout to the server and rebuild the stack.
#
#   scripts/deploy.sh --dry-run     preview the files that would be copied
#   scripts/deploy.sh               copy, rebuild, recreate, wait for health
#
# Never copies .env — the server keeps its own.
set -euo pipefail

REMOTE="${PALADMIN_REMOTE:-paladmin-host}"
REMOTE_DIR="/opt/stacks/paladmin"
FILES=(app.py compose.yaml Dockerfile requirements.txt README.md templates docs)

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

[ -f .env ] || echo "warning: no local .env (not copied anyway — the server keeps its own)" >&2
[ -f app.py ] || { echo "error: run from the repository root" >&2; exit 1; }

if [ "$DRY" = 1 ]; then
  echo "would copy to ${REMOTE}:${REMOTE_DIR}"
  for f in "${FILES[@]}"; do
    if [ -d "$f" ]; then
      find "$f" -type f -not -path '*/__pycache__/*' | sed 's/^/  /'
    else
      echo "  $f"
    fi
  done
  echo "then: docker compose up -d --build"
  exit 0
fi

echo "==> copying files"
remote_stage="${REMOTE_DIR}/.incoming"
ssh "$REMOTE" "mkdir -p '${remote_stage}'"

# tar over ssh keeps permissions and avoids one round-trip per file
tar -czf - --exclude='__pycache__' "${FILES[@]}" | ssh "$REMOTE" "tar -xzf - -C '${remote_stage}'"

echo "==> installing"
ssh "$REMOTE" "set -e
  cd '${remote_stage}'
  # move into place; .env and data/ live only on the server and stay untouched
  for f in app.py compose.yaml Dockerfile requirements.txt README.md; do
    [ -f \"\$f\" ] && cp -f \"\$f\" '${REMOTE_DIR}/'
  done
  rm -rf '${REMOTE_DIR}/templates' '${REMOTE_DIR}/docs'
  [ -d templates ] && cp -a templates '${REMOTE_DIR}/'
  [ -d docs ] && cp -a docs '${REMOTE_DIR}/'
  rm -rf '${remote_stage}'
  cd '${REMOTE_DIR}'
  docker compose up -d --build >/dev/null
"

echo "==> waiting for health"
for _ in $(seq 1 30); do
  status=$(ssh "$REMOTE" "docker inspect --format '{{.State.Health.Status}}' paladmin 2>/dev/null || echo missing")
  if [ "$status" = "healthy" ]; then
    echo "==> paladmin is healthy — https://pal-admin.example.com"
    exit 0
  fi
  sleep 2
done

echo "error: container did not become healthy in 60s" >&2
ssh "$REMOTE" "docker logs --tail 40 paladmin 2>&1 | tail -40" >&2 || true
exit 1
