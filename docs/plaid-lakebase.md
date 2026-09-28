# Plaid → Lakebase: serving the lakehouse's Plaid data to VirtuWill

Plaid balances and transactions land in the lakehouse every day. The Databricks job
**`ingestion_plaid_financials`** (07:30 America/Chicago) runs the *plaid_integration*
notebook. The notebook pulls USAA, Chase, Fidelity and Amex from Plaid, writes each pull
as JSONL to `/Volumes/prod/bronze/stage/plaid/…`, and Auto Loader lands it in
`prod.bronze.raw_plaid_balances` / `raw_plaid_transactions`. This page covers what happens
next: **reverse ETL** from Delta into the app's own Lakebase database, where the Money screens
read Plaid data like every other finance record.

```
Plaid API ─▶ stage volume (JSONL) ─▶ prod.bronze.raw_plaid_* ─▶ prod.silver.plaid_* ─▶ synced tables ─▶ finance.*
            └──────────── plaid_integration notebook ───────────┘ └────── jobs/plaid_lakebase_refresh.py ───────┘
```

| Piece | Where | What it does |
|---|---|---|
| Serving tables | [`lakehouse/plaid_serving_create.sql`](../lakehouse/plaid_serving_create.sql), [`plaid_serving_refresh.sql`](../lakehouse/plaid_serving_refresh.sql) | bronze → `prod.silver.plaid_transactions` (one row per transaction) and `prod.silver.plaid_balances` (every pull), with primary keys and the Change Data Feed |
| Synced tables | Databricks (set up once, below) | mirror the serving tables into the app's Lakebase database as `public.plaid_transactions` / `public.plaid_balances` |
| The mirror loader | [`virtuwill/plaid_mirror.py`](../virtuwill/plaid_mirror.py) | reads what changed in the synced tables and loads it into `finance.*`, through the same matching as Money › Imports |
| Daily refresh | [`jobs/plaid_lakebase_refresh.py`](../jobs/plaid_lakebase_refresh.py) | a second task in `ingestion_plaid_financials`, after the notebook: serving tables → refresh synced tables → ask the app to load |

## Why the app loads the synced tables instead of reading them directly

Every Money screen, the Today screen, budgets and receipts read `finance.transactions` and
`finance.balance_snapshots`. The mirror loader puts Plaid's rows there, so nothing on the
screens changes, and a Plaid transaction gets everything an imported one gets: categories
you set stick, receipts match it, and a statement import later finds it instead of adding it
twice. The synced tables stay exactly what the lakehouse has. Postgres writes go only to the
app's own tables.

## 1. Serving tables (`prod.silver`)

Bronze is append-only and not deduplicated. It also holds the API's values as the notebook
wrote them: `_pulled_at`, `date` and `authorized_date` as text, and a missing date as the
text `'None'`. Triggered sync needs a **primary key** and the **Change Data Feed** on its
source. The serving tables are typed, and have both:

- `prod.silver.plaid_transactions`: the current state of each `transaction_id`. The latest
  sync of it wins, and a transaction Plaid **removed** is deleted. Key: `transaction_id`.
- `prod.silver.plaid_balances`: every balance pull, for history. Key: `(account_id, as_of)`.

`plaid_serving_create.sql` declares them (`CREATE TABLE IF NOT EXISTS`, safe to run every
day). `plaid_serving_refresh.sql` then **MERGEs** into them and doesn't replace them. Replacing a table rewrites it whole, and each
triggered sync would copy every row again. A MERGE writes only what changed, and a row is
rewritten only when bronze has a newer pull of it.

> Delta primary keys are informational (not enforced). The refresh's dedup keeps them
> unique. Keep names and columns lowercase, as synced tables expect.

**Pending charges.** Bronze doesn't keep Plaid's `pending_transaction_id`. When a pending
charge settles, Plaid gives the settled charge a new id and marks the pending one
*removed*. The refresh deletes the pending row from the serving table. The app then deletes
it from `finance.transactions` and adds the settled one. A category you set on the pending
row doesn't carry over. To keep it, have the notebook also write
`"pending_transaction_id": t.get("pending_transaction_id")` (and add it to
`transactions_schema`, with `.option("mergeSchema", "true")` on the bronze write), then add
the column to both SQL files. The app picks it up by itself and updates the pending row in
place.

**Categories.** The notebook writes Plaid's detailed category code
(`FOOD_AND_DRINK_GROCERIES`), or its primary code, into `category`. The app maps these the
same way as the direct Plaid job. Transactions that only have Plaid's older category path
(`Food and Drink > Restaurants`) carry no code, so they land in **Review**.

**Accounts.** An account is matched by its last four digits (`mask`). One with no four-digit
mask is skipped and named in the load's report. Give it one with `PLAID_MASKS` on the app.
Fidelity retirement accounts come in as retirement accounts with their plan (Roth IRA,
IRA, 401(k), 403(b), 401(a)). Other investment accounts come in as brokerage.

## 2. Synced tables into the app's Lakebase database

Create one synced table per serving table. Put both in the **same database the app is
attached to** (`databricks_postgres` by default), schema `public`:

| Source | Synced table (Postgres) | Primary key | Mode |
|---|---|---|---|
| `prod.silver.plaid_transactions` | `public.plaid_transactions` | `transaction_id` | Triggered |
| `prod.silver.plaid_balances` | `public.plaid_balances` | `account_id, as_of` | Triggered |

Use **Triggered** mode. The data changes once a day, so a refresh started by the daily job
is enough and you don't pay for an always-on pipeline. (Snapshot mode replaces the whole
table each run. Continuous mode keeps a pipeline running.)

**UI:** Catalog → `prod.silver.plaid_transactions` → **Create** → **Synced table** → the
app's Lakebase database, schema `public`, mode **Triggered**, key `transaction_id`. Do the
same for `plaid_balances`.

**CLI:** first check which kind of Lakebase you have. If `databricks postgres list-projects`
lists your project, it is **Autoscaling**: use `databricks postgres create-synced-table`.
Otherwise it is **Provisioned**: use `databricks database create-synced-database-table`.
Terraform has `databricks_database_synced_database_table`.

A different schema works too. Set `PLAID_MIRROR_SCHEMA` on the app to match it.

## 3. Let the app read them

The app already signs in to Lakebase as its own service principal, with a fresh OAuth token
for every connection (`virtuwill/db.py`). It needs no new credentials, only read access to
the two synced tables. In the Lakebase SQL editor, as the tables' owner:

```sql
GRANT USAGE  ON SCHEMA public TO "<app service principal client id>";
GRANT SELECT ON public.plaid_transactions, public.plaid_balances TO "<app service principal client id>";
```

The client ID is on the app's **Authorization** tab (it is also the app's `PGUSER`).
The app can only read these tables. It never writes to them.

**Settings → Diagnostics** shows **Sync · plaid_mirror** after the first load. If a table is
missing or the grant was forgotten, it names the table. If a synced table is ever deleted
and made again, run the `GRANT` again.

## 4. The daily refresh: a second task in `ingestion_plaid_financials`

```
07:30 CT  ingestion_plaid_financials
  task 1  plaid_integration notebook     Plaid → stage volume → prod.bronze.raw_plaid_*
  task 2  jobs/plaid_lakebase_refresh.py (depends on task 1)
            1. MERGE into prod.silver.plaid_*
            2. refresh both synced tables, wait for them
            3. POST /api/ingest/v1/finance/plaid-mirror  → finance.*
```

In the job's **Tasks** tab, add a task:

- **Type:** Python script. **Source:** Git provider, this repo, branch `main`.
  **Path:** `jobs/plaid_lakebase_refresh.py`.
- **Depends on:** the notebook task.
- **Compute:** the same serverless or cluster the notebook uses. It needs `databricks-sdk`,
  which Databricks runtimes include.
- **Parameters:** `["--secret-scope", "plaid_integration"]` (this is also the default).

Add these secrets to the `plaid_integration` scope. Keys can use underscores, like the
notebook's own keys:

| Secret key | Value |
|---|---|
| `plaid_synced_tables` | the synced tables' Unity Catalog names, comma-separated |
| `plaid_sync_pipeline_ids` | optional: their pipeline ids, if the lookup by name fails |
| `virtuwill_url` | the app's address, `https://virtuwill-….databricksapps.com` |
| `virtuwill_token` | an API token from **Settings → API access**, with **finance:write** |
| `databricks_host`, `databricks_client_id`, `databricks_client_secret` | a service principal that can use the app (see [the ingest API](finance-api.md)) |

The job runs as you, so it can already read bronze, write `prod.silver` and run the synced
tables' pipelines. If a synced table fails to refresh, the task stops before step 3 and fails
the run.

Commands, for trying it by hand from a notebook or terminal:

```bash
python jobs/plaid_lakebase_refresh.py              # all three steps
python jobs/plaid_lakebase_refresh.py --dry-run    # ask the app what it would load; nothing loaded
python jobs/plaid_lakebase_refresh.py --only app   # just step 3
```

From the workspace, `POST /api/v1/money/plaid-mirror/sync` (signed in) does step 3, and
`GET /api/v1/money/plaid-mirror` shows what the mirror holds and how much isn't loaded yet.

### What each load does

- **Transactions** synced since the last load are matched on Plaid's id and added or
  updated. A pending charge updates when it settles.
- **Balances** pulled since the last load become one reading per account, day (in
  `APP_TIMEZONE`) and kind: the current balance, plus the available balance for bank
  accounts. A card's balance is what's owed.
- **Pending charges Plaid has dropped** are removed from `finance.transactions`: ones that
  settled under a new id, or were voided. A row a receipt points at is left alone.
- An account with no four-digit `mask` is skipped and listed in the report. Give it one with
  `PLAID_MASKS` (`{"<plaid account_id>": "1234"}`) on the app.

The loader remembers how far it has read (`finance.plaid_mirror`), so a repeated call loads
nothing new. Each load appears in **Money › Imports** as `Lakebase · Plaid · <time>`.

## One path for Plaid, not two

`jobs/plaid_to_virtuwill.py` is a different way in: it calls Plaid itself and posts to the
ingest API, with no lakehouse involved. The notebook pipeline replaces it. Don't schedule
both. They would pull Plaid twice, and the mirror loader would remove pending charges the
other path loaded but the lakehouse doesn't have.

## Security

- The app's Postgres role only has `SELECT` on the synced tables. It can't change served data.
- Restrict `prod.silver.plaid_*` and `prod.bronze.raw_plaid_*` in Unity Catalog to the job's
  principal and the owner. `plaid_items` (secret names and cursors) is never served.
- No long-lived database passwords anywhere. The app mints an OAuth token for each
  connection, and the job's secrets live in the secret scope.

## References

- Lakebase: https://docs.databricks.com/aws/en/oltp/projects/
- Synced tables: https://docs.databricks.com/aws/en/oltp/instances/sync-data/sync-table
- Connecting an app (OAuth token rotation): https://docs.databricks.com/aws/en/oltp/projects/external-apps-connect
