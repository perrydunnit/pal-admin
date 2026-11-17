#!/usr/bin/env bash
# Publish this checkout to the server and rebuild the stack.
#
#   scripts/deploy.sh               verify, copy, rebuild, wait for health
#   scripts/deploy.sh --dry-run     preview the files that would be copied
#   scripts/deploy.sh --skip-verify don't run scripts/verify.sh first
#   scripts/deploy.sh --allow-dirty deploy even with uncommitted changes
#   scripts/deploy.sh --rollback    redeploy the currently deployed commit
#
# Never copies .env — the server keeps its own. Records the deployed commit in
# ${REMOTE_DIR}/DEPLOYED so the live version is always identifiable.
set -euo pipefail

REMOTE="${PALADMIN_REMOTE:-paladmin-host}"
REMOTE_DIR="/opt/stacks/paladmin"
FILES=(app.py musiclib.py palconfig.py compose.yaml Dockerfile requirements.txt README.md .dockerignore frontend docs)

DRY=0 SKIP_VERIFY=0 ALLOW_DIRTY=0 ROLLBACK=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n)   DRY=1 ;;
    --skip-verify)  SKIP_VERIFY=1 ;;
    --allow-dirty)  ALLOW_DIRTY=1 ;;
    --rollback)     ROLLBACK=1 ;;
    -h|--help)      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

[ -f .env ] || echo "warning: no local .env (not copied anyway — the server keeps its own)" >&2
[ -f app.py ] || { echo "error: run from the repository root" >&2; exit 1; }

# Redeploy whatever DEPLOYED names, from a throwaway worktree so the caller's
# checkout, branch and uncommitted work are never touched.
if [ "$ROLLBACK" = 1 ]; then
  target=$(ssh "$REMOTE" "sed -n 's/^commit=//p' '${REMOTE_DIR}/DEPLOYED' 2>/dev/null" || true)
  [ -n "${target:-}" ] || { echo "error: no commit recorded in ${REMOTE_DIR}/DEPLOYED" >&2; exit 1; }
  git cat-file -e "${target}^{commit}" 2>/dev/null \
    || { echo "error: ${target} is not in this clone; fetch it first" >&2; exit 1; }

  WT=$(mktemp -d)/rollback
  git worktree add --detach "$WT" "$target" >/dev/null
  trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT

  echo "==> rolling back to ${target:0:7}: $(git log -1 --format=%s "$target")"
  cd "$WT"
  # a commit that predates the gate cannot be verified by it
  ALLOW_DIRTY=1
  SKIP_VERIFY=1
fi

if [ "$DRY" = 1 ]; then
  echo "would copy to ${REMOTE}:${REMOTE_DIR}"
  for f in "${FILES[@]}"; do
    if [ -d "$f" ]; then find "$f" -type f -not -path '*/__pycache__/*' \
      -not -path '*/node_modules/*' -not -path '*/dist/*' | sed 's/^/  /'
    else echo "  $f"; fi
  done
  echo "then: docker compose up -d --build"
  exit 0
fi

if [ -n "$(git status --porcelain)" ] && [ "$ALLOW_DIRTY" = 0 ]; then
  echo "error: working tree has uncommitted changes." >&2
  git status --short | sed 's/^/  /' >&2
  echo "commit them (preferred), or pass --allow-dirty to deploy anyway." >&2
  exit 1
fi

if [ "$SKIP_VERIFY" = 0 ]; then
  echo "==> verify"
  scripts/verify.sh || { echo "error: verification failed; nothing deployed." >&2; exit 1; }
fi

commit=$(git rev-parse HEAD)
subject=$(git log -1 --format=%s)
dirty=""
[ -n "$(git status --porcelain)" ] && dirty=" (dirty)"

echo "==> copying ${commit:0:7}${dirty} — ${subject}"
remote_stage="${REMOTE_DIR}/.incoming"
ssh "$REMOTE" "rm -rf '${remote_stage}' && mkdir -p '${remote_stage}'"

# tar over ssh keeps permissions and avoids one round-trip per file
tar -czf - --exclude='__pycache__' --exclude='node_modules' --exclude='frontend/dist' \
  "${FILES[@]}" | ssh "$REMOTE" "tar -xzf - -C '${remote_stage}'"

echo "==> installing"
ssh "$REMOTE" "set -e
  cd '${remote_stage}'
  # move into place; .env and data/ live only on the server and stay untouched
  for f in app.py musiclib.py palconfig.py compose.yaml Dockerfile requirements.txt README.md .dockerignore; do
    [ -f \"\$f\" ] && cp -f \"\$f\" '${REMOTE_DIR}/'
  done
  rm -rf '${REMOTE_DIR}/templates' '${REMOTE_DIR}/docs' '${REMOTE_DIR}/frontend'
  [ -d docs ] && cp -a docs '${REMOTE_DIR}/'
  [ -d frontend ] && cp -a frontend '${REMOTE_DIR}/'
  rm -rf '${remote_stage}'
  cd '${REMOTE_DIR}'
  docker compose up -d --build >/dev/null
"

echo "==> waiting for health"
for _ in $(seq 1 30); do
  status=$(ssh "$REMOTE" "docker inspect --format '{{.State.Health.Status}}' paladmin 2>/dev/null || echo missing")
  if [ "$status" = "healthy" ]; then
    {
      printf 'commit=%s\n' "$commit"
      printf 'subject=%s\n' "$subject"
      printf 'deployed=%s\n' "$(date -Is)"
      printf 'deployed_by=%s\n' "$(whoami)"
    } | ssh "$REMOTE" "cat > '${REMOTE_DIR}/DEPLOYED'"
    echo "==> deployed ${commit:0:7}${dirty} — https://pal-admin.example.com"
    exit 0
  fi
  sleep 2
done

echo "error: container did not become healthy in 60s" >&2
ssh "$REMOTE" "docker logs --tail 40 paladmin 2>&1 | tail -40" >&2 || true
exit 1
