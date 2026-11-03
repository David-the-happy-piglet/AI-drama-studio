# Database migrations

Run Alembic commands from `ai-drama-studio/backend/`. Alembic reads
`DATABASE_URL`; when it is unset, it uses the local SQLite URL from
`alembic.ini`.

## New or empty database

```bash
alembic upgrade head
```

## Existing database created with `create_all`

Revision `20260915_01` represents the legacy schema created by `create_all`.
Revision `20260915_02` adds durable generation request fields, worker leases,
and persisted output metadata. Do not run the baseline `upgrade` against a
database that already contains these tables.

First back up the database. On a restored copy, stamp the legacy baseline, run
the bridge migration, and compare the result with the current models:

```bash
alembic stamp 20260915_01
alembic upgrade head
alembic check
alembic current
```

Only when that copy preserves its data and `alembic check` reports
`No new upgrade operations detected`, repeat the same sequence on the real
database during a maintenance window:

```bash
alembic stamp 20260915_01
alembic upgrade head
alembic current
```

`stamp` only writes the revision marker; revision `20260915_02` performs the
schema and data migration. Never stamp a database whose restored copy cannot
complete the full sequence. Use the explicit baseline ID instead of
`stamp head`, because `head` would skip the bridge and could also mark future
migrations as applied.

## Add a migration

After editing the models, generate and review a revision before applying it:

```bash
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
alembic check
```

In deployment, run `alembic upgrade head` once as a migration job before
starting the API or workers.
