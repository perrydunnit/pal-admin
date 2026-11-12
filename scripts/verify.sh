#!/usr/bin/env bash
# Repository quality gate. Run before every commit and every deploy.
#
#   scripts/verify.sh            all checks
#   scripts/verify.sh --fast     skip the slow/optional ones (used by the pre-commit hook)
#
# Exit code 0 means the tree is safe to commit and deploy.
set -uo pipefail

cd "$(dirname "$0")/.."

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

fail=0
pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
skip() { printf '  \033[33mskip\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }

PY=$(command -v python3 || command -v python || true)
NODE=$(command -v node || true)

echo "verify: $(pwd)"

# ---------------------------------------------------------------- 1. python
if [ -z "$PY" ]; then
  bad "python not found"
else
  tmp=$(mktemp -d)
  if "$PY" -m py_compile app.py 2>"$tmp/err"; then
    pass "app.py compiles"
  else
    bad "app.py does not compile: $(tail -3 "$tmp/err")"
  fi

  # A local module imported by app.py must also be shipped: the Dockerfile has
  # to COPY it and deploy.sh has to transfer it. Missing this took the site down
  # once (musiclib.py), and py_compile alone cannot see it.
  if "$PY" - >"$tmp/ship" 2>&1 <<'PY'
import ast, pathlib, sys

local = {
    alias.name
    for node in ast.walk(ast.parse(pathlib.Path("app.py").read_text(encoding="utf-8")))
    if isinstance(node, ast.Import)
    for alias in node.names
    if pathlib.Path(alias.name + ".py").exists()
}
dockerfile = pathlib.Path("Dockerfile").read_text(encoding="utf-8")
deploy = pathlib.Path("scripts/deploy.sh").read_text(encoding="utf-8")
problems = [f"{m}.py" for m in sorted(local) if m not in dockerfile or m not in deploy]
for p in problems:
    print(f"not shipped: {p} (Dockerfile COPY and scripts/deploy.sh FILES)")
sys.exit(1 if problems else 0)
PY
  then
    pass "local modules are shipped by Dockerfile and deploy"
  else
    bad "$(tr '\n' ' ' <"$tmp/ship")"
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- 2. python tests
if [ -z "$PY" ]; then
  skip "python tests (python not found)"
elif [ ! -d tests ]; then
  skip "python tests (no tests/ directory)"
else
  tmp=$(mktemp -d)
  if "$PY" -m unittest discover -s tests -p 'test_*.py' >"$tmp/out" 2>&1; then
    pass "python unit tests ($(grep -oE 'Ran [0-9]+ tests?' "$tmp/out" | head -1))"
  else
    bad "python unit tests: $(tail -5 "$tmp/out" | tr '\n' ' ')"
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- 3. frontend
NPM=$(command -v npm || true)
if [ -z "$NODE" ] || [ -z "$NPM" ]; then
  skip "frontend (node/npm not installed)"
elif [ ! -d frontend/node_modules ]; then
  skip "frontend (run 'npm install' in frontend/)"
else
  tmp=$(mktemp -d)
  if (cd frontend && "$NPM" test --silent >"$tmp/test" 2>&1); then
    pass "frontend unit tests"
  else
    bad "frontend unit tests: $(tail -4 "$tmp/test" | tr '\n' ' ')"
  fi
  if [ "$FAST" = 1 ]; then
    skip "frontend build (fast mode)"
  elif (cd frontend && "$NPM" run build --silent >"$tmp/build" 2>&1); then
    pass "frontend builds"
  else
    bad "frontend build: $(tail -4 "$tmp/build" | tr '\n' ' ')"
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- 3. shell
status=0
for s in scripts/*.sh .githooks/*; do
  [ -f "$s" ] || continue
  bash -n "$s" 2>/dev/null || { bad "shell syntax: $s"; status=1; }
done
[ "$status" = 0 ] && pass "shell scripts parse"

# ---------------------------------------------------------------- 4. secrets
tracked_env=$(git ls-files --error-unmatch .env 2>/dev/null)
if [ -n "$tracked_env" ]; then
  bad ".env is tracked by git"
else
  pass ".env is not tracked"
fi

if "$PY" - <<'PY'
import re, subprocess, sys

files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
problems = []

# any committed env file other than the documented template
for f in files:
    if re.fullmatch(r"(^|.*/)\.env$", f):
        problems.append(f"committed env file: {f}")

# obvious inline credentials in tracked files
patterns = [
    # KEY=value at end of line (env files, compose, yaml) — code like
    # os.environ.get(...) ends with a paren and therefore never matches
    re.compile(r"""(?ix)
        \b(ADMIN_PASSWORD|SERVER_PASSWORD|PASSWORD|API_?KEY|SECRET|TOKEN)\s*[=:]\s*
        ["']?([A-Za-z0-9_\-+/=]{8,})["']?\s*(?:\#.*)?$
    """),
]
placeholders = ("change-me", "changeme", "placeholder", "redacted", "example", "your-", "xxxx")
allow = (".env.example",)
for f in files:
    if f in allow or not re.search(r"\.(py|sh|ya?ml|md|html|json|txt|example)$|(^|/)Dockerfile$", f):
        continue
    try:
        text = open(f, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        continue
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if any(p in low for p in placeholders) or "os.environ" in line or "getenv" in line:
            continue
        if patterns[0].search(line.rstrip()):
            problems.append(f"{f}:{n}: possible secret")

# the live password must never appear anywhere
try:
    env = open(".env", encoding="utf-8").read()
    for line in env.splitlines():
        if line.startswith("ADMIN_PASSWORD="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 8:
                for f in files:
                    try:
                        if value in open(f, encoding="utf-8").read():
                            problems.append(f"{f}: contains the live ADMIN_PASSWORD")
                    except (OSError, UnicodeDecodeError):
                        pass
except FileNotFoundError:
    pass

for p in problems:
    print(p)
sys.exit(1 if problems else 0)
PY
then
  pass "no secrets in tracked files"
else
  bad "secret scan failed (see above)"
fi

# ---------------------------------------------------------------- 5. docs
if "$PY" - <<'PY'
import re, sys
from pathlib import Path

bad = []
root = Path(".")
for md in [root / "README.md", *sorted((root / "docs").glob("*.md"))]:
    text = md.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        if not (md.parent / target).exists():
            bad.append(f"{md.as_posix()} -> {target}")
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
PY
then
  pass "doc links resolve"
else
  bad "broken doc links"
fi

# ---------------------------------------------------------------- 6. compose
if [ "$FAST" = 1 ]; then
  skip "compose config (fast mode)"
elif ! command -v docker >/dev/null 2>&1; then
  skip "compose config (docker not installed)"
elif [ ! -f .env ]; then
  skip "compose config (no local .env)"
else
  if docker compose config -q >/dev/null 2>&1; then
    pass "compose config resolves"
  else
    bad "docker compose config failed"
  fi
fi

echo
if [ "$fail" = 0 ]; then
  echo "verify: PASS"
else
  echo "verify: FAIL"
fi
exit "$fail"
