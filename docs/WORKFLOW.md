# Development workflow

The loop this repo expects, whether the change comes from a person or an agent.
It is deliberately short: one branch per change, verified before commit, verified
again before deploy, and the deployed commit recorded.

```
branch → change → verify → commit → deploy → confirm live → merge
```

## 1. Branches

`main` is always deployable.

```bash
git switch -c feat/session-export
```

Small doc-only or typo fixes may go straight to `main`; anything that changes
behaviour gets a branch, so a bad change can be reverted without archaeology.

Naming: `feat/…`, `fix/…`, `chore/…`, `docs/…` — matching the commit type.

## 2. Change

- Follow [ENHANCEMENTS.md](ENHANCEMENTS.md) — the constraints there are not
  suggestions.
- Keep scope tight. One idea per branch and per commit.
- Update the docs the change touches (`docs/API.md` for endpoints,
  `docs/ARCHITECTURE.md` for ingestion, `docs/DEPLOYMENT.md` for anything
  operational). A change that alters behaviour without updating docs is
  incomplete.

## 3. Verify

```bash
scripts/verify.sh
```

Checks: `app.py` compiles, the template's JS parses, shell scripts are valid,
`.env` is untracked, no tracked file contains a live secret, doc links resolve,
and `docker compose config` still resolves.

`--fast` skips the compose check; that is what the pre-commit hook runs.

Install the hook once per clone:

```bash
scripts/install-hooks.sh     # sets core.hooksPath=.githooks
```

For behaviour changes, add a check that would have caught the bug — the pure
functions (`split_by_day`, `_clip_session`, `range_payload`,
`timeline_payload`) are the cheap place to start.

## 4. Commit

Conventional Commits, imperative subject, ≤72 characters:

```
feat: add a zoom control to the timeline
fix: stop the sticky date lagging a day boundary
docs: explain how to rotate the admin password
chore: bump flask to 3.1
```

Body: **why**, not what — the diff already says what. Mention the user-visible
effect and anything surprising. Reference the issue or the symptom if there is
one.

One logical change per commit. If a commit needs "and also", it is two commits.

Never commit `.env`, `data/`, or anything under `__pycache__` — `.gitignore`
covers them, and `verify.sh` fails if `.env` ever becomes tracked.

## 5. Deploy

```bash
scripts/deploy.sh            # verifies, refuses dirty trees, copies, rebuilds, waits for health
```

It copies only the deployable files — `app.py`, `templates/`, `docs/`,
`compose.yaml`, `Dockerfile`, `requirements.txt`, `README.md` — into
`/opt/stacks/paladmin`, never `.env` or `data/`, then rebuilds and polls the
healthcheck.

Outcomes:

| Result | Meaning |
|---|---|
| `deployed abc1234 — https://…` | live, and `/opt/stacks/paladmin/DEPLOYED` records commit, subject, time, deployer |
| `error: working tree has uncommitted changes` | commit first, or `--allow-dirty` if you truly mean it |
| `error: verification failed; nothing deployed` | fix the gate, nothing shipped |
| `error: container did not become healthy in 60s` | code is on the server but unhealthy; logs are printed, fix and re-run |

Deploying this stack never touches the game server. See
[DEPLOYMENT.md](DEPLOYMENT.md) for rollback and database backups.

## 6. Confirm live

Health is necessary, not sufficient. After deploying:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u "admin:$PASS" https://pal-admin.example.com/   # expect 200
curl -s -o /dev/null -w '%{http_code}\n' https://pal-admin.example.com/                    # expect 401
ssh paladmin-host 'cat /opt/stacks/paladmin/DEPLOYED'
```

If the change touches the UI, load the page and look at it — the sticky timeline
bug that prompted this workflow was invisible to every automated check.

## 7. Merge

```bash
git switch main
git merge --no-ff feat/session-export
```

Deploy from `main` afterwards so what is live matches what is on the mainline.

## Rolling back

```bash
git revert <sha>              # preferred: keeps history honest
git switch --detach <good-sha> && scripts/deploy.sh --allow-dirty
```

Or restore a previous commit's tree onto `main`:

```bash
git checkout <good-sha> -- .
git commit -m "revert: restore <good-sha>"
scripts/deploy.sh
```

Data rollback is separate — see [DEPLOYMENT.md](DEPLOYMENT.md#back-up-or-copy-the-database).

## Definition of done

- [ ] Behaviour verified, not just "it compiles"
- [ ] `scripts/verify.sh` passes
- [ ] Docs updated for anything that changed
- [ ] One logical commit, conventional message explaining why
- [ ] Deployed with `scripts/deploy.sh`
- [ ] Live check confirms health **and** the intended behaviour
- [ ] `DEPLOYED` on the server names the commit you think is live

## Continuous integration

`.github/workflows/verify.yml` runs the same gate on push and pull request once
this repo has a remote. Nothing depends on it today; it exists so the gate is
enforced the same way when it does.
