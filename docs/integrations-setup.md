# Integrations: identities, secrets and grants

Everything Databricks needs so that scheduled jobs can load data into VirtuWill:

- **Plaid**: a notebook job pulls balances and transactions, keeps the raw responses in a volume, and writes to the Lakebase database.
- **Spotify**: a job reads the streaming-history files you drop into a volume.

This page names identities, scopes and keys only. Secret values never go in
this repository (it is public), in a notebook, or in a job's parameters.

Placeholders used below:

| Placeholder | What it is | Where to find it |
|---|---|---|
| `<app-sp>` | The app's own service principal, created by Databricks Apps. It owns the database tables, because it runs the schema migrations. | App page → **Authorization** (its Application ID) |
| `<finance-sp>` | The service principal the Plaid job runs as | Settings → Identity and access → Service principals |
| `<music-sp>` | The service principal the Spotify job runs as | Same place |
| `workspace.virtuwill` | The catalog and schema for volumes and Delta tables (the Plaid job's default) | Catalog Explorer |

In Unity Catalog grants, a service principal is written as its Application ID
in backticks. In Postgres it is the same Application ID in double quotes.

## At a glance

| | Plaid job | Spotify job | App (exists today) |
|---|---|---|---|
| Runs as | `<finance-sp>` | `<music-sp>` | `<app-sp>` |
| Secret scope | `virtuwill` (READ) | none needed | `virtuwill` (READ, through app resources) |
| Volumes | `plaid`: read and write | `spotify`: read (write only if it moves processed files) | none |
| Delta tables | none required | `workspace.virtuwill`: create and modify | none |
| Lakebase | A Postgres role, plus rights on `finance` | A Postgres role, plus rights on `music`, only if it writes there | Owns every schema |
| Outside services | Plaid API | None; Spotify's export is a manual download | None |

## 1. Service principals

Create one service principal per domain, so finance data is kept apart from the
rest: **Settings → Identity and access → Service principals → Add**.

| Service principal | Used for | OAuth secret? |
|---|---|---|
| `virtuwill-finance-jobs` (`<finance-sp>`) | Runs the Plaid job | **No**, for direct database writes: a job running as the principal already signs in as it. **Yes** (with the `apps` scope) only while the job still posts through the ingest API; see [finance-api.md](finance-api.md) §2. The feed principal made there can be reused as this one. |
| `virtuwill-music-jobs` (`<music-sp>`) | Runs the Spotify job | No |
| `<app-sp>` | The app itself | Managed by Databricks; its secret is never shown |

For each job principal:

- **Your access to it:** to pick it as a job's **Run as**, you need the
  **Service Principal: User** role on it (the principal's **Permissions** tab).
  Workspace admins have this already.
- **Entitlements:** **Workspace access** only, never admin. Jobs on serverless
  compute need no cluster-create entitlement.
- **The code:** if the job runs from a Git folder in the workspace, give the
  principal **Can read** on that folder. If the job's source is the Git
  repository itself, nothing is needed.

## 2. Secret scope and keys

One Databricks-backed scope, `virtuwill` (the Plaid job reads the scope named by
`VIRTUWILL_SECRET_SCOPE`; the default is `virtuwill`). Create it with
`databricks secrets create-scope virtuwill`, then set each key with
`databricks secrets put-secret virtuwill <key>`.

Scope permissions (`databricks secrets put-acl`):

| Principal | Permission |
|---|---|
| You | MANAGE (as the creator) |
| `<finance-sp>` | READ |
| `<app-sp>` | READ, given when the app's secret resources are attached |
| `<music-sp>` | None |

Keys:

| Key | Used by | Notes |
|---|---|---|
| `secret-key` | App | Flask session key, 32+ characters. Exists today. |
| `admin-password` | App | 12+ characters. Exists today. |
| `plaid-client-id` | Plaid job | From the Plaid dashboard |
| `plaid-secret` | Plaid job | Use the secret for the environment you run in |
| `plaid-env` | Plaid job | `sandbox` or `production`. Not really secret, but kept with the others. |
| `plaid-access-tokens` | Plaid job | JSON such as `{"Chase": "access-production-…"}`: one access token per linked institution. The names become the institution names. |
| `plaid-masks` | Plaid job | Optional JSON `{"<plaid account_id>": "1234"}` for accounts Plaid gives no four-digit mask |
| `virtuwill-url` | Plaid job | **Ingest-API mode only.** The app's address |
| `virtuwill-token` | Plaid job | **Ingest-API mode only.** A `vw_…` token from Settings → API access |
| `databricks-host` | Plaid job | **Ingest-API mode only.** The workspace address |
| `databricks-client-id` | Plaid job | **Ingest-API mode only.** `<finance-sp>`'s Application ID |
| `databricks-client-secret` | Plaid job | **Ingest-API mode only.** `<finance-sp>`'s OAuth secret. Note its expiry date. |

The Spotify file job needs no secrets. Pulling from Spotify's Web API instead of
export files would add `spotify-client-id`, `spotify-client-secret` and
`spotify-refresh-token`, with READ for `<music-sp>`. That API only returns the
last 50 plays, though, so the export files remain the source for history.

Settings that are not secret, such as the database host and name, the volume
paths and the time zone, go in the job's parameters or environment, not in the
scope.

## 3. Unity Catalog: volumes and tables

Create the volumes, under **Catalog → workspace → virtuwill → Create → Volume**
(managed):

| Volume | Path | Holds |
|---|---|---|
| `plaid` | `/Volumes/workspace/virtuwill/plaid/` | `state.json`: the sync cursors, which the job needs and already uses. `raw/<yyyy-mm-dd>/<institution>.json`: each run's Plaid responses (bronze). |
| `spotify` | `/Volumes/workspace/virtuwill/spotify/` | `inbox/`: where you drop `Streaming_History_Audio_*.json` from Spotify's "Extended streaming history" export. `processed/`: optional, if the job moves files after loading them. |

Grants, run in the SQL editor:

```sql
-- Both job principals
GRANT USE CATALOG ON CATALOG workspace TO `<finance-sp>`;
GRANT USE CATALOG ON CATALOG workspace TO `<music-sp>`;
GRANT USE SCHEMA  ON SCHEMA  workspace.virtuwill TO `<finance-sp>`;
GRANT USE SCHEMA  ON SCHEMA  workspace.virtuwill TO `<music-sp>`;

-- Plaid: cursors and raw responses
GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.virtuwill.plaid TO `<finance-sp>`;

-- Spotify: read the drops (add WRITE VOLUME if the job moves files to processed/)
GRANT READ VOLUME ON VOLUME workspace.virtuwill.spotify TO `<music-sp>`;

-- Spotify: bronze and silver Delta tables for plays
GRANT CREATE TABLE ON SCHEMA workspace.virtuwill TO `<music-sp>`;
-- The job owns the tables it creates, so it can modify them. If you create them instead:
-- GRANT SELECT, MODIFY ON TABLE workspace.virtuwill.<table> TO `<music-sp>`;
```

Your own account needs `CREATE VOLUME` on the schema to make the volumes; as
the workspace or catalog owner you have it already.

## 4. Lakebase (the app's Postgres database)

A job writing straight to the database needs three things:

1. **Can use** on the database instance.
2. A Postgres role for its principal.
3. Rights on the schemas it writes to.

The tables belong to `<app-sp>`. Run the grants as the instance owner (you, a
`databricks_superuser`), in the Lakebase SQL editor or `psql`.

1. **Instance permission:** Compute → Lakebase → your instance → **Permissions**
   → add `<finance-sp>` (and `<music-sp>` if it writes to the database) with
   **Can use**. This is what lets a job request a database login.
2. **Postgres role:** instance → **Roles** → **Add role** → pick the
   principal. Or in SQL:

   ```sql
   CREATE EXTENSION IF NOT EXISTS databricks_auth;
   SELECT databricks_create_role('<finance-sp>', 'SERVICE_PRINCIPAL');
   SELECT databricks_create_role('<music-sp>', 'SERVICE_PRINCIPAL');   -- only if needed
   ```

3. **Schema rights.** The finance loader writes only to `finance.*`:

   ```sql
   GRANT USAGE ON SCHEMA finance TO "<finance-sp>";
   GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA finance TO "<finance-sp>";
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA finance TO "<finance-sp>";
   -- Tables the app adds in later schema versions get the same rights automatically:
   ALTER DEFAULT PRIVILEGES FOR ROLE "<app-sp>" IN SCHEMA finance
     GRANT SELECT, INSERT, UPDATE ON TABLES TO "<finance-sp>";
   ALTER DEFAULT PRIVILEGES FOR ROLE "<app-sp>" IN SCHEMA finance
     GRANT USAGE, SELECT ON SEQUENCES TO "<finance-sp>";
   ```

   For `<music-sp>`, use the same grants on `music`, but only once there are play
   tables there for it to write. No `DELETE` and no `CREATE` for either job:
   schema changes stay with the app.

How the job signs in to the database: it runs as the principal, asks Databricks
for a short-lived database credential for the instance, and uses the
principal's Application ID as the Postgres user. There is no stored password
and no secret. Pass the instance name, host and database name
(`databricks_postgres` by default) as job parameters.

> **Code change needed before direct writes.** Today the first database
> connection in `virtuwill.db` also runs pending schema migrations. That is
> right for the app, but a job must not do it; with the grants above, it
> couldn't. The job entry point needs to open the database without migrating,
> and to get its password from the instance's database credential rather than
> the app's token. Until that lands, the Plaid job posts through the ingest API
> (the "Ingest-API mode only" keys above).

## 5. Your own access, to explore the data

- **Register the database in Unity Catalog:** instance → **Register in Unity
  Catalog** (named, say, `virtuwill_pg`). Postgres schemas then appear in
  Catalog Explorer and can be queried from the SQL editor, notebooks and
  dashboards, read-only.
- Grant `USE CATALOG`, `USE SCHEMA` and `SELECT` on `virtuwill_pg` to whoever
  should explore it; by default that's only you. Don't give it to the job
  principals; they don't need it.

## 6. The jobs

| | Plaid | Spotify |
|---|---|---|
| Task | Notebook or Python script: `jobs/plaid_to_virtuwill.py` | Notebook or Python script (to come) |
| Compute | Serverless, with `plaid-python` as a dependency | Serverless |
| Run as | `<finance-sp>` | `<music-sp>` |
| Trigger | Daily schedule | File arrival on `/Volumes/workspace/virtuwill/spotify/inbox/` |
| First run | `--dry-run`: counts only, nothing saved | Dry run on one file |

## Checklist

- [ ] `virtuwill-finance-jobs` and `virtuwill-music-jobs` created, with you as Service Principal: User
- [ ] Scope `virtuwill`: Plaid keys added; READ for `<finance-sp>`
- [ ] Volumes `plaid` and `spotify` created; UC grants run
- [ ] Lakebase: **Can use**, Postgres roles, schema grants and default privileges
- [ ] Database registered in Unity Catalog, for exploring
- [ ] Jobs created with **Run as** set, and a dry run passed
- [ ] OAuth secret expiry noted (ingest-API mode only)
