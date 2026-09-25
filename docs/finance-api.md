# Finance ingest API

A scheduled job — for example a Claude task that runs every morning — can push
account balances and transactions into VirtuWill without signing in. Loads go
through the same matching as **Money › Imports** and show up there, labelled with
the token that sent them.

## 1. Make a VirtuWill token

This happens inside VirtuWill itself, not in the Databricks portal. Open the
app, sign in, and go to **Settings → API access → + New token** in VirtuWill's
own sidebar. Name it after the job (e.g. *Claude daily finance*). The token
(`vw_…`) is shown **once**; store it where the job keeps secrets. Revoke it
there at any time. Only a hash of it is kept in the database.

Scopes: `finance:write` (push data) and `finance:read` (see what's loaded). A job
that only pushes can go without `finance:read`, but reading the status first lets
it send only what's new.

## 2. Let the job through Databricks sign-in

The app sits behind Databricks sign-in, so a job needs its own Databricks
identity as well. The app's own service principal (on the app's Authorization
page) is not it: that one is what the app runs as, and its secret is never shown.

1. **Make a service principal for the job.** Workspace **Settings → Identity and
   access → Service principals → Add** (e.g. `virtuwill-financials-feed`). It
   needs no admin access. It doesn't need SQL access either, and may not need
   workspace access (test before removing that one).
2. **Give it an OAuth secret.** On its **Secrets** tab, **Generate secret** with
   the **apps** scope rather than all-APIs. Note the Application (client) ID and
   the secret (shown once), and when the secret expires.
3. **Let it use the app.** On the app page, **Share** → add the service principal
   with **Can use**. This is what grants access; the secret alone does not.

Each call then carries two headers:

- `Authorization: Bearer <Databricks token>`: a short-lived token the job gets by
  posting `grant_type=client_credentials&scope=apps` to
  `https://<workspace>/oidc/v1/token`, signed in with the client ID and secret.
- `X-VirtuWill-Token: vw_…`: the VirtuWill token.

`scripts/finance_feed.py` does both (standard library only):

```sh
export VIRTUWILL_URL=https://virtuwill-….databricksapps.com VIRTUWILL_TOKEN=vw_…
export DATABRICKS_HOST=https://….cloud.databricks.com DATABRICKS_CLIENT_ID=… DATABRICKS_CLIENT_SECRET=…
python scripts/finance_feed.py status                   # a safe first check
python scripts/finance_feed.py push today.json --dry-run
python scripts/finance_feed.py push today.json
```

A Claude cloud task keeps these as environment variables in its environment's
settings, and needs the app's host and the workspace host allowed in the
environment's network access.

| What you see | What it means |
|---|---|
| `Databricks sign-in failed` | Wrong client ID or secret, or a scope the secret wasn't made with. Set `DATABRICKS_SCOPE` to match. |
| `… not JSON. The service principal needs 'Can use'…` | Databricks turned the call away before it reached the app: share the app with the service principal. |
| `That token isn't valid or has been revoked` | The `vw_…` token is wrong or was revoked in VirtuWill's Settings. |

## 3. Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| GET | `/api/ingest/v1` | finance:read | Describes this API: fields, kinds, limits |
| GET | `/api/ingest/v1/finance/status` | finance:read | Each account (last four only): latest balance and date, last transaction date, count; the last 10 API loads |
| POST | `/api/ingest/v1/finance` | finance:write | Balances and/or transactions as JSON (below) |
| POST | `/api/ingest/v1/finance/files` | finance:write | Raw exports as `multipart/form-data` field `files` (PDF statements, CSV exports, `.finance.json`) |

Add `"dry_run": true` (or form field `dry_run=true` for files) to see what would
change without loading anything.

### Body

```json
{
  "source": "claude-daily",
  "accounts": [
    {"mask": "1234", "institution": "Example Bank", "name": "Everyday card", "account_type": "credit_card"}
  ],
  "balances": [
    {"account_mask": "1234", "as_of": "2026-09-25", "balance": 812.40, "kind": "reported"}
  ],
  "transactions": [
    {"account_mask": "1234", "posted_on": "2026-09-24", "transacted_on": "2026-09-23",
     "description": "CORNER CAFE", "amount": 6.75, "category": "Dining",
     "external_id": "bank-txn-8f2c", "pending": false},
    {"account_mask": "1234", "posted_on": "2026-09-24", "description": "PAYMENT THANK YOU",
     "amount": -500.00, "kind": "card_payment"}
  ]
}
```

- **Amounts:** positive means money leaving the account (purchases, payments out); negative means money coming in (refunds, deposits, a card payment received).
- **Card and loan balances:** the amount owed, as a positive number.
- **`kind`:** `expense` (default), `refund`, `card_payment`, `transfer` or `income`.
- **`category`:** optional; uncategorised expenses go to *Review*.
- **Accounts:** a balance or transaction may name an account that hasn't been listed. It's created on first sight, and its name and type can be set later in Money.
- **Limits:** 5 MB and 5,000 records per request.

### Sending again is safe

- **An identical request** changes nothing: the response says it was skipped.
- **Overlapping runs** are matched, not doubled. With `external_id` (the bank's or aggregator's transaction id) the match is exact. Without it, a transaction matches one with the same account and amount within 3 days.
- **A pending charge** sent with `"pending": true` is updated in place when it's later sent posted with the same `external_id`, even if its date or amount changed.
- **A balance** sent again for the same account, day and kind replaces the earlier reading.

### Responses

| Status | Meaning |
|---|---|
| `201` | Loaded. `report` gives counts: new, seen (already there), taken over from the Finance tracker, balances. |
| `200` | Dry run (`preview`), or an identical request that was skipped. |
| `400` | Something in the body is wrong; `error` says what, e.g. `transactions[3]: 'soon' is not a date`. |
| `401` / `403` | Missing, invalid or revoked token / the token lacks the scope. |
| `413` | Body over 5 MB. |

## 4. A scheduled Claude task

A prompt for a daily routine (with the five variables above in its environment):

> Each morning: run `python scripts/finance_feed.py status`. For each account, gather today's balance
> and every transaction since three days before its `last_transaction_on` from
> *[your source: bank export, aggregator, email statements]*. Include each
> transaction's id from the source as `external_id`, and mark unsettled ones
> `"pending": true`. Write them as one JSON body with `"source": "claude-daily"`
> and load it with `python scripts/finance_feed.py push today.json`. If the
> response is 400, fix what `error` names and retry once. Report the counts from
> `report`.
