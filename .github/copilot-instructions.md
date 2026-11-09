# Copilot instructions for this repository.

The full working agreement lives in [AGENTS.md](../AGENTS.md) and the process in
[docs/WORKFLOW.md](../docs/WORKFLOW.md). The essentials:

- **Loop: branch → change → verify → commit → deploy → confirm live → merge.**
  Do all of it in one go; do not stop after editing files.
- **Verify with `scripts/verify.sh`** before committing and again inside
  `scripts/deploy.sh` before shipping.
- **Commit** in Conventional Commits, one logical change per commit, body
  explaining why, ending with
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- **Deploy** with `scripts/deploy.sh`. It refuses a dirty tree, rebuilds the
  `paladmin` stack on `paladmin-host`, waits for health, and records the commit in
  `/opt/stacks/paladmin/DEPLOYED`.
- **Confirm live**: `https://pal-admin.example.com` returns 401 without
  credentials and 200 with them, and the new behaviour is actually visible.
  Report the deployed sha with evidence.

Never commit `.env` or `data/`. Never restart the game server to deploy this.
Read [docs/ENHANCEMENTS.md](../docs/ENHANCEMENTS.md) before changing behaviour.
