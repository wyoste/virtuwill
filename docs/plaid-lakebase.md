# Plaid → Lakebase → VirtuWill's finance tables

Everything runs inside the Databricks workspace:

```
Plaid API ─▶ stage volume ─▶ prod.bronze.raw_plaid_*  ─▶  Lakebase synced tables  ─▶  finance.accounts / transactions / balance_snapshots
          └─ plaid_integration notebook (07:30 job) ─┘   └─ synced-table pipeline ─┘   └─ the app, every 15 minutes ─┘
```

1. The **`ingestion_plaid_financials`** job runs the *plaid_integration* notebook, which lands
   each Plaid pull in `prod.bronze.raw_plaid_balances` / `raw_plaid_transactions`.
2. **Synced tables** mirror those two bronze tables into the app's Lakebase database, read-only.
3. **The app** checks the synced tables every `PLAID_SYNC_MINUTES` (15 in `app.yaml`). It loads
   what has landed since its last load into the finance tables that Money and Today read
   ([`virtuwill/plaid_synced.py`](../virtuwill/plaid_synced.py)).

The app writes only its own tables and never changes the synced ones. It needs no job, no
API token and no secrets. It already signs in to Lakebase as its own service principal.

## What goes where

| Synced table | VirtuWill |
|---|---|
| `raw_plaid_balances`: each account's latest pull | `finance.accounts`: matched by the last four digits (`mask`), created on first sight, institution = `item_label` |
| `raw_plaid_balances`: pulls since the last load | `finance.balance_snapshots`: the current balance, plus the available balance for bank accounts, one reading per account and day (America/Chicago). A card's balance is what's owed. |
| `raw_plaid_transactions`: each transaction touched since the last load, at its latest sync | `finance.transactions`: matched on Plaid's `transaction_id`, so a re-sent or modified one updates in place |
| … whose latest `_sync_op` is `removed` | deleted from `finance.transactions` (unless a receipt points at it) |

- **"Since the last load"** is judged by Auto Loader's `_ingested_at` (or `_pulled_at` if that
  column wasn't synced). How far the app has read is kept in `finance.plaid_bronze_load`
  and saved in the same transaction as the load. A failed load changes nothing and is
  retried next time. Loads are matched, so reading rows again never doubles anything.
- **Categories:** Plaid's code maps to the app's categories (`FOOD_AND_DRINK_GROCERIES` →
  Groceries, `…_COFFEE` → Dining, `INCOME_*` → a payroll deposit, a card payment → a card
  payment). The older `Food and Drink > Restaurants` paths carry no code, so those
  transactions land in **Review**. A category you set in the app is kept.
- **Pending charges:** when one settles, Plaid adds the settled charge under a new id and
  removes the pending one. The app does the same.
- **Accounts:** Fidelity retirement accounts come in with their plan (Roth IRA, IRA, 401(k),
  403(b), 401(a)). Other investment accounts come in as brokerage. An account with no
  four-digit mask is skipped and named in the report. Give it one with `PLAID_MASKS`
  (`{"<plaid account_id>": "1234"}`) in `app.yaml`.
- **Money › Imports** lists each load as `Lakehouse · Plaid · <time>`.

## Setting it up

1. **Tell the app where the synced tables are.** By default it reads
   `public.raw_plaid_balances` and `public.raw_plaid_transactions` in the database it's
   attached to. If yours have other names, set these in `app.yaml`:
   `PLAID_SYNCED_SCHEMA`, `PLAID_SYNCED_BALANCES`, `PLAID_SYNCED_TRANSACTIONS`. The synced
   tables must be in the **same Lakebase database** as the app (`databricks_postgres` by
   default).
2. **Let the app read them.** In the Lakebase SQL editor, as the synced tables' owner (you),
   use the app's service principal client ID from the app's **Authorization** tab:

   ```sql
   GRANT USAGE  ON SCHEMA <schema> TO "<app client id>";
   GRANT SELECT ON <schema>.raw_plaid_balances, <schema>.raw_plaid_transactions TO "<app client id>";
   ```

   If a synced table is ever deleted and made again, run the `GRANT` again.
3. **Deploy the app from this branch.** On start it adds `finance.plaid_bronze_load` and
   starts the 15-minute loop.
4. **Check it.** Signed in, open `/api/v1/money/plaid-sync`. It shows:
   - the tables it's reading, how many rows they have, and how many aren't loaded yet;
   - the result of the last load;
   - `plaid_tables_here`: every table with "plaid" in its name, which helps if the names
     in step 1 are off.

   To preview a load, `POST` `{"dry_run": true}` to the same address. **Settings →
   Diagnostics** shows the latest load as **Sync · plaid_bronze**, including any error.

`POST /api/v1/money/plaid-sync` with `{}` loads right away; `{"reload": true}` reads everything
again. `PLAID_SYNC_MINUTES=0` turns the loop off.

**Synced table mode:** the app reads only what's new by `_ingested_at`, so Triggered,
Continuous and Snapshot mode all work. Triggered, refreshed after the 07:30 notebook, is
enough for data that changes once a day.

## The older direct Plaid job

`jobs/plaid_to_virtuwill.py` calls Plaid itself and posts to the app's ingest API. The
notebook pipeline replaces it. Don't schedule both. Both use the same mapping
(`virtuwill/importers/plaid.py`).
