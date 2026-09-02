# account-service

Mirrors Clerk users into Postgres and serves profile data to other internal services.

Clerk is the system of record for identity — the frontend handles signup and login. This
service subscribes to Clerk's user webhooks, keeps a local copy of each user, and exposes
that copy so sibling services (job aggregator, finance tracker) can read profile data
without calling Clerk on every request.

**This service does not authenticate its callers.** Request auth lives in the gateway.
The only thing verified here is the *signature* on incoming Clerk webhooks.

## Stack

FastAPI · SQLAlchemy 2.0 (async) · Alembic · asyncpg · Postgres 17 · svix

> **Why not Prisma?** The original plan called for it, but `prisma-client-py` last shipped
> in August 2024 and supports Python 3.12 at most, while this project runs 3.14.
> SQLAlchemy + Alembic is the maintained equivalent. `schema.prisma` has no counterpart
> here; the schema lives in `app/models.py` and migrations in `alembic/versions/`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhooks/clerk` | Clerk webhook receiver. Svix-signed. Always answers `204` once the signature checks out. |
| `GET` | `/users/{clerk_id}` | Profile lookup for internal services. `404` when unknown or soft-deleted. |
| `GET` | `/users/{clerk_id}?include_deleted=true` | Same, but returns soft-deleted tombstones for callers resolving historical references. |
| `GET` | `/health` | Readiness probe. `503` when Postgres is unreachable. |

Interactive docs at `/docs`, enabled only when `ENV=local`.

## Running locally

### With compose (from `central/api`)

This repo is a submodule of `central`. The `compose.yml` in `central/api` owns the
Postgres instance and this service. What this service needs from it:

```yaml
services:
  db:
    image: postgres:17
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: root
      POSTGRES_DB: central
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U root -d central"]
      interval: 5s
      timeout: 5s
      retries: 10

  account:
    build:
      context: ./account-service
      dockerfile: Dockerfile
    # Single source of local config: the same file the host tooling reads.
    env_file:
      - ./account-service/.env
    environment:
      # The one value that differs between host and compose network. "db" is the
      # service name above, overriding the @localhost the .env uses outside Docker.
      DATABASE_URL: postgresql+asyncpg://root:root@db:5432/account_service
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres_data:
```

The per-service databases are created once, on first volume init, by `central/api/init-db.sql`:

```sql
CREATE DATABASE account_service OWNER root;
CREATE DATABASE account_service_test OWNER root;
```

The container runs `alembic upgrade head` on boot, so no migration step is needed.

There is exactly one `.env`, this repo's own, and compose reads it through `env_file:`.
It is gitignored, so a fresh clone needs it created before the stack will start:

```bash
cd account-service && cp .env.example .env   # then set CLERK_WEBHOOK_SECRET
cd .. && docker compose up -d
```

Only `DATABASE_URL` is overridden in `compose.yml`, because the hostname genuinely
differs between the two runtimes (`localhost` on the host, `db` inside the network).
Every other variable has a single owner, so the file and the container cannot drift.

### With a local venv (faster iteration)

Postgres still comes from compose; only the app runs on the host. Rebuilding the
image on every edit costs ~40s, so this is the loop to use while writing code.

```bash
cd ../ && docker compose up -d db    # just Postgres
cd account-service
uv venv --python 3.14
uv pip install -e ".[dev]"

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The `.env` already points at `localhost:5432` for this path — that is the value
compose overrides when the same file is loaded into the container.

Point `DATABASE_URL` at `localhost:5432` for this path, not `db:5432`.

## Testing the webhook

### Fast loop — no Clerk account, no tunnel

`scripts/send_test_webhook.py` builds a realistic Clerk payload, signs it with your local
`CLERK_WEBHOOK_SECRET`, and POSTs it:

```bash
uv run python scripts/send_test_webhook.py user.created --clerk-id user_test_1
curl localhost:8000/users/user_test_1

uv run python scripts/send_test_webhook.py user.updated --clerk-id user_test_1
uv run python scripts/send_test_webhook.py user.deleted --clerk-id user_test_1

# Signature rejection — expect HTTP 400 and no row written.
uv run python scripts/send_test_webhook.py user.created --bad-signature

# Idempotency — reuse a Svix message id; the second call is a no-op.
uv run python scripts/send_test_webhook.py user.updated --svix-id msg_fixed
uv run python scripts/send_test_webhook.py user.updated --svix-id msg_fixed

# Out-of-order delivery — a backdated update must not overwrite newer data.
uv run python scripts/send_test_webhook.py user.updated --updated-minutes-ago 60
```

### Real Clerk events

1. Expose the local port: `svix listen http://localhost:8000/webhooks/clerk` (or `ngrok http 8000`).
2. In the Clerk dashboard: **Configure → Webhooks → Add Endpoint**, paste the forwarding URL
   with `/webhooks/clerk` appended.
3. Subscribe to `user.created`, `user.updated`, `user.deleted`.
4. Copy the **Signing Secret** (`whsec_…`) into `CLERK_WEBHOOK_SECRET` and restart the app.

### Automated suite

Needs a running Postgres and a database separate from your dev one — the suite drops and
recreates every table between tests.

```bash
# Created automatically by init-db.sql; recreate it only if you wiped the volume.
uv run pytest
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
uv run alembic downgrade -1
```

`alembic/env.py` reads `DATABASE_URL` through `app/config.py`, so `alembic.ini` carries no
connection string and local/Railway use the same variable.

## Data model

`users` — keyed by `clerk_id`. Everything but the bookkeeping columns is mirrored from
Clerk and is not authoritative.

| Column | Notes |
|---|---|
| `clerk_id` | Primary key. Clerk's user id. |
| `email` | Nullable — Clerk permits phone-only users, and the delete payload carries no email. |
| `email_verified`, `primary_email_id` | Derived from the address matching `primary_email_address_id`. |
| `first_name`, `last_name`, `username`, `image_url` | Straight mirrors. |
| `last_sign_in_at`, `clerk_created_at`, `clerk_updated_at` | Clerk's own timestamps (sent as milliseconds, converted on ingest). |
| `created_at`, `updated_at` | This service's row bookkeeping — distinct from Clerk's. |
| `deleted_at` | Soft delete. |

A partial unique index, `UNIQUE (email) WHERE deleted_at IS NULL`, keeps active emails
unique while letting a deleted user's address be reused by a new signup.

`webhook_events` — one row per processed Svix message id, for idempotency.

## Webhook processing guarantees

Clerk retries on any non-2xx and does not guarantee ordering, so the handler is built
around three rules:

1. **Signature first.** The route reads the raw request body and declares no Pydantic body
   parameter — FastAPI would parse and re-serialise the payload, and the changed bytes
   would no longer match Svix's HMAC. Svix also rejects timestamps outside ±5 minutes.
2. **Exactly-once effects.** The Svix message id is claimed in `webhook_events` in the same
   transaction as the user mutation, so a replay is a no-op and a mid-processing crash
   rolls both back for a clean retry.
3. **Deletion is terminal, staleness loses.** The upsert only applies when the row is not
   soft-deleted *and* the incoming `clerk_updated_at` is newer than the stored one. A
   delayed `user.updated` can neither resurrect a deleted user nor overwrite newer data.

Authentic-but-unparseable payloads are logged and answered `204`: a retry cannot fix them,
and anything else makes Clerk retry for days.

## Deploying to Railway

- Add a Postgres plugin and set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`. Railway
  hands out a `postgresql://…` URL, sometimes with `?sslmode=require`; `app/config.py`
  rewrites the scheme for asyncpg and converts `sslmode` into a driver argument.
- Set `CLERK_WEBHOOK_SECRET` and `ENV=production`.
- `PORT` is injected by Railway and honoured by the entrypoint.
- Healthcheck path: `/health`.
- Migrations run on container start. If this service is ever scaled past one replica,
  move `alembic upgrade head` out of `docker-entrypoint.sh` into a release step.

## Deferred on purpose

- **`plan`, `role`, `preferences`** — the profile fields other services will eventually
  read. Left out until a consumer actually needs them; each is one Alembic migration.
- **Surrogate primary key.** `clerk_id` is the PK today. If the service should outlive its
  dependence on Clerk, add a UUID `id`, backfill, and repoint consumers — mechanical, but
  cheaper to do before other services store `clerk_id` references.
- **Batch lookup** (`GET /users?clerk_ids=…`) for callers resolving many users at once.
