# Plaid → Lakebase: serving the lakehouse's Plaid data to VirtuWill

Plaid balances and transactions land in the lakehouse every day. This is how they reach
the app: **reverse ETL** from Delta into the app's own Lakebase database, where the Money
screens read them like every other finance record.

```
prod.bronze.raw_plaid_*  ─▶  prod.silver.plaid_*  ─▶  Lakebase synced tables  ─▶  finance.* (VirtuWill)
  (append-only raw)          (keyed, current state)    public.plaid_* (read-only)     Money, Today
```

| Piece | Where | What it does |
|---|---|---|
| Serving tables | [`lakehouse/plaid_serving_create.sql`](../lakehouse/plaid_serving_create.sql), [`plaid_serving_refresh.sql`](../lakehouse/plaid_serving_refresh.sql) | bronze → `prod.silver.plaid_transactions` (one row per transaction) and `prod.silver.plaid_balances` (every pull), with primary keys and the Change Data Feed |
| Synced tables | Databricks (set up once, below) | mirror the serving tables into the app's Lakebase database as `public.plaid_transactions` / `public.plaid_balances` |
| The mirror loader | [`virtuwill/plaid_mirror.py`](../virtuwill/plaid_mirror.py) | reads what changed in the synced tables and loads it into `finance.*`, through the same matching as Money › Imports |
| Daily refresh | [`jobs/plaid_lakebase_refresh.py`](../jobs/plaid_lakebase_refresh.py) | after the Plaid pull: serving tables → refresh synced tables → ask the app to load |

## Why the app loads the synced tables instead of reading them directly

Every Money screen, the Today screen, budgets and receipts read `finance.transactions` and
`finance.balance_snapshots`. The mirror loader puts Plaid's rows there, so nothing on the
screens changes, and a Plaid transaction gets everything an imported one gets: categories
you set stick, receipts match it, and a statement import later finds it instead of adding it
twice. The synced tables stay exactly what the lakehouse has. Postgres writes go only to the
app's own tables.

## 1. Serving tables (`prod.silver`)

Bronze is append-only and not deduplicated, and triggered sync needs a **primary key** and
the **Change Data Feed** on its source. The serving tables have both:

- `prod.silver.plaid_transactions`: the current state of each `transaction_id`. The latest
  sync of it wins, and a transaction Plaid **removed** is deleted. Key: `transaction_id`.
- `prod.silver.plaid_balances`: every balance pull, for history. Key: `(account_id, as_of)`.

`plaid_serving_create.sql` makes them once. `plaid_serving_refresh.sql` then **MERGEs**
into them daily and doesn't replace them. Replacing a table rewrites it whole, and each
triggered sync would copy every row again. A MERGE writes only what changed, and a row is
rewritten only when bronze has a newer pull of it.

> Delta primary keys are informational (not enforced). The refresh's dedup keeps them
> unique. Keep names and columns lowercase, as synced tables expect.

**Optional, recommended:** if bronze keeps Plaid's `pending_transaction_id`, add it to
`plaid_transactions` (in both SQL files). The loader uses it when it's there: a charge that
settles under a new id then updates its pending row in place, with the same id the direct
Plaid job (`jobs/plaid_to_virtuwill.py`) gives it. Without it, the loader deletes the pending
row once Plaid drops it and adds the settled one, so you get the same result, but a category
you set on the pending row doesn't carry over.

The loader reads the category from `category`, or from `personal_finance_category` if
present. It accepts Plaid's code (`FOOD_AND_DRINK_GROCERIES` or just `FOOD_AND_DRINK`) or its
`{primary, detailed}` object as JSON. Plaid's older category lists (`["Food and Drink", …]`)
carry no code, so those transactions land in **Review**.

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

## 4. The daily refresh

Add a task to the end of the daily Plaid job, after the pull lands in bronze:

```
07:30 CT:  Plaid pull → bronze  →  plaid_lakebase_refresh.py
                                    1. MERGE into prod.silver.plaid_*
                                    2. refresh both synced tables, wait for them
                                    3. POST /api/ingest/v1/finance/plaid-mirror  → finance.*
```

The task is a Python script task on `jobs/plaid_lakebase_refresh.py` (from a Git folder or a
Git-sourced job), on a cluster with `databricks-sdk`. Its settings come from the
`virtuwill` secret scope, the same scope the Plaid job uses:

| Secret key | Value |
|---|---|
| `plaid-synced-tables` | the synced tables' Unity Catalog names, comma-separated |
| `plaid-sync-pipeline-ids` | optional: their pipeline ids, if the lookup by name fails |
| `virtuwill-url`, `virtuwill-token`, `databricks-host`, `databricks-client-id`, `databricks-client-secret` | as for [the ingest API](finance-api.md); the token needs **finance:write** |

The job's own identity needs to be able to run the synced tables' pipelines, and to read
bronze and write `prod.silver`. If a synced table fails to refresh, the job stops before
step 3 and exits non-zero.

Commands:

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

`jobs/plaid_to_virtuwill.py` pulls Plaid itself and posts to the ingest API. Once the
lakehouse path is running, **stop that job** (or keep it only for `--dry-run` checks). Both
paths give a transaction the same id when `pending_transaction_id` is served (above). But
running both means two copies of the pull, and the mirror loader would remove pending
charges the other path loaded but the lakehouse doesn't have yet.

## Security

- The app's Postgres role only has `SELECT` on the synced tables. It can't change served data.
- Restrict `prod.silver.plaid_*` and `prod.bronze.raw_plaid_*` in Unity Catalog to the job's
  principal and the owner. `plaid_items` (access tokens, cursors) is never served.
- No long-lived database passwords anywhere. The app mints an OAuth token for each
  connection, and the job's secrets live in the secret scope.

## References

- Lakebase: https://docs.databricks.com/aws/en/oltp/projects/
- Synced tables: https://docs.databricks.com/aws/en/oltp/instances/sync-data/sync-table
- Connecting an app (OAuth token rotation): https://docs.databricks.com/aws/en/oltp/projects/external-apps-connect
