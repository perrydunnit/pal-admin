# paladmin — Palworld admin dashboard

Shows live player state and a calendar of who was online when, for a
[thijsvanloef/palworld-server-docker](https://github.com/thijsvanloef/palworld-server-docker)
server. Managed as a Dockge stack.

## Setup

```bash
cp .env.example .env     # then fill in ADMIN_PASSWORD
docker compose up -d --build
```

Secrets live in `.env`, which is gitignored. `compose.yaml` also carries the
Traefik basic-auth hash for the `admin` user.

## Operations

```bash
docker compose up -d --build     # apply code/UI changes
docker compose logs -f           # collector warnings show here
```

Restarting this stack has no effect on the game server.
