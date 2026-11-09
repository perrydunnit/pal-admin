# AGENTS.md

Instructions for AI agents (and people) working in this repository. These are
requirements, not suggestions.

## The loop

Every change follows this, without being asked:

1. **Branch** — `git switch -c feat/<slug>` for anything behavioural.
2. **Change** — smallest correct edit; update the docs it touches.
3. **Verify** — `scripts/verify.sh` must pass.
4. **Commit** — one logical change, Conventional Commits, body explains *why*.
5. **Deploy** — `scripts/deploy.sh` (it re-verifies, then rebuilds the stack).
6. **Confirm live** — health **and** the actual behaviour. Report the commit.
7. **Merge** — back to `main` with `--no-ff`.

Never leave verified work uncommitted. Never leave committed work undeployed
unless the user says otherwise; if you cannot deploy, say so explicitly.

## Commit messages

```
<type>: <imperative summary ≤72 chars>

Why this change, in prose. What the user sees. Anything surprising.
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`.

Include the trailer:

```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

## Hard rules

- **Never commit `.env`.** It holds the live passwords. `.gitignore` covers it;
  `verify.sh` fails if it becomes tracked. `.env.example` is the template.
- **Never commit `data/`** — that is the live SQLite database.
- **Never restart the game server to deploy this.** They are separate stacks.
  If a change genuinely needs a game restart, stop and ask the user first;
  someone may be playing.
- **Do not use `docker restart` on the game container.** `docker compose stop`
  (30 s grace) is the only safe stop; `docker restart` can SIGKILL mid-save.
- **Keep it one gunicorn worker.** The collector thread assumes a single writer.
- **No new runtime dependency without need.** The UI has no build step by
  design; keep it that way.

## Before changing behaviour

Read [docs/ENHANCEMENTS.md](docs/ENHANCEMENTS.md). It lists the constraints that
have already caused bugs: single-worker assumption, game-local log timestamps,
name-matched leave events, sticky elements needing `width: max-content`,
fraction-based positioning, and the diffed-refresh rule.

## Verifying properly

`scripts/verify.sh` is a floor, not a ceiling. It proves the code parses and
that nothing secret leaked; it cannot prove the timeline looks right. For UI
changes, load the page and inspect real values. For ingestion changes, exercise
the affected endpoint and read the JSON.

If you add a bug, add the check that would have caught it.

## Reporting

State the commit sha you deployed and paste the live evidence (status codes,
endpoint output, or what the screen shows). "It should work" is not a report.
