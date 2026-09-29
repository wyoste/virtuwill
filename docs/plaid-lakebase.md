# Plaid → Lakebase: loading the lakehouse's Plaid data into VirtuWill

Everything runs inside the Databricks workspace. The Databricks job **`ingestion_plaid_financials`**
(07:30 America/Chicago) runs the *plaid_integration* notebook. The notebook pulls USAA, Chase,
Fidelity and Amex from Plaid and lands them in `prod.bronze`. A second task in the same job then
writes the new rows straight into the app's Lakebase database, in the tables the Money and Today
screens already read.

```
Plaid API ─▶ stage volume ─▶ prod.bronze.raw_plaid_* ─▶ finance.accounts / transactions / balance_snapshots
          └───── plaid_integration notebook ───────┘ └──── jobs/plaid_bronze_to_lakebase.py ────┘
```

| Piece | Where | What it does |
|---|---|---|
| The load task | [`jobs/plaid_bronze_to_lakebase.py`](../jobs/plaid_bronze_to_lakebase.py) | reads what landed in bronze since the last run (Spark), connects to Lakebase, loads it |
| The mapping and matching | [`virtuwill/importers/plaid.py`](../virtuwill/importers/plaid.py), [`plaid_load.py`](../virtuwill/importers/plaid_load.py) | Plaid's fields → the finance model, through the same staging and matching as Money › Imports |
| Where it stopped | `finance.plaid_bronze_load` ([`db/schema/105_plaid_bronze.sql`](../db/schema/105_plaid_bronze.sql)) | how far bronze has been read, saved in the same transaction as the load |

No API calls, no synced tables and no secrets. The task reads bronze with the job's Spark
session. It signs in to Lakebase as the identity the job runs as, using a short-lived token
from the Databricks SDK.

## What goes where

| Bronze | VirtuWill (Lakebase) |
|---|---|
| `raw_plaid_balances`: each account's latest pull | `finance.accounts`: matched by the last four digits (`mask`), created on first sight, institution = `item_label` |
| `raw_plaid_balances`: pulls since the last run | `finance.balance_snapshots`: the current balance, plus the available balance for bank accounts, one reading per account and day (in America/Chicago). A card's balance is what's owed. |
| `raw_plaid_transactions`: each transaction touched since the last run, at its latest sync | `finance.transactions`: matched on Plaid's `transaction_id`, so a re-sent or modified one updates in place |
| … whose latest `_sync_op` is `removed` | deleted from `finance.transactions` (unless a receipt points at it) |

- **Categories:** Plaid's detailed code (`FOOD_AND_DRINK_GROCERIES` → Groceries, `…_COFFEE`
  → Dining, `INCOME_*` → a payroll deposit, a card payment → a card payment). The older
  `Food and Drink > Restaurants` paths carry no code, so those transactions land in
  **Review**. A category you set in the app is kept.
- **Pending charges:** when a pending charge settles, Plaid adds the settled charge under a
  new id and removes the pending one. The load does the same. To keep a category you set on
  the pending row, have the notebook also write Plaid's `pending_transaction_id`. The load
  uses it when it's there and updates the pending row in place.
- **Accounts:** Fidelity retirement accounts come in with their plan (Roth IRA, IRA, 401(k),
  403(b), 401(a)). Other investment accounts come in as brokerage. An account with no
  four-digit mask is skipped and named in the report. Give it one with `--masks`.
- **Money › Imports** lists each load as `Lakehouse · Plaid · <time>`. **Settings → Diagnostics**
  shows the latest one as **Sync · plaid_bronze**.

## Setting it up

1. **Deploy the app from this branch.** On start it adds `finance.plaid_bronze_load`. The
   task refuses to run without it.
2. **Add the task.** In `ingestion_plaid_financials` → **Tasks** → **Add task**:
   - **Type:** Python script. **Source:** Git provider (this repo, branch `main`), or the
     repo's Git folder in the workspace. **Path:** `jobs/plaid_bronze_to_lakebase.py`.
   - **Depends on:** the notebook task.
   - **Compute:** the same as the notebook. Add the library `psycopg[binary]` (PyPI) to it.
     Serverless: put it in the task's environment.
   - **Parameters**, using the name from the Lakebase page:
     - Provisioned: `["--instance", "<instance name>"]`
     - Autoscaling: `["--endpoint", "projects/<project>/branches/<branch>/endpoints/<endpoint>"]`

     (To tell which you have: if `databricks postgres list-projects` lists your database,
     it's Autoscaling.)
3. **Try it first:** run the task alone with `--dry-run` added to the parameters. It
   prints what it would add and match, and writes nothing.
4. Run it for real. The next 07:30 run carries on from there.

Other parameters: `--database` (default `databricks_postgres`), `--catalog prod`,
`--schema bronze`, `--timezone America/Chicago`, `--masks '{"<plaid account_id>": "1234"}'`,
and `--reload` to read all of bronze again (matched, so nothing doubles).

## Permissions

The task signs in to Lakebase as the job's **Run as** identity (you). The finance tables
belong to the app's own role, which made them on first start. Your role needs to be able to
read and write them:

- If your role is an admin of the Lakebase instance (the instance's creator is), it may be
  able to already. If it can't, the first real run stops with *"Lakebase refused the write"*
  and changes nothing, because the whole load is one transaction.
- If it can't, the table owner (the app) has to grant your role `SELECT, INSERT, UPDATE,
  DELETE` on the `finance` tables, `USAGE` on their sequences, and write on
  `virtuwill.sync_reports`. Or run the task as the app's service principal instead.

## If a run fails

Nothing is saved: not the rows, and not how far bronze was read. The next run reads the
same rows again. Loads are matched, so running again never doubles anything.

## The older direct Plaid job

`jobs/plaid_to_virtuwill.py` calls Plaid itself and posts to the app's ingest API. The
notebook pipeline replaces it. Don't schedule both. Both use the same mapping
(`virtuwill/importers/plaid.py`).
