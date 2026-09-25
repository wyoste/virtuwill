# Finance ingest API

A scheduled job — for example a Claude task that runs every morning — can push
account balances and transactions into VirtuWill without signing in. Loads go
through the same matching as **Money › Imports** and show up there, labelled with
the token that sent them.

## 1. Make a token

Workspace → **Settings → API access → + New token**. Name it after the job
(e.g. *Claude daily finance*). The token (`vw_…`) is shown **once**; store it
where the job keeps secrets. Revoke it there at any time. Only a hash of it is
kept in the database.

Scopes: `finance:write` (push data) and `finance:read` (see what's loaded). A job
that only pushes can go without `finance:read`, but reading the status first lets
it send only what's new.

## 2. Reach the app

Send the token in the `X-VirtuWill-Token` header. (`Authorization: Bearer vw_…`
also works when nothing in front of the app uses that header itself.)

If the app sits behind Databricks sign-in, the request also needs a Databricks
OAuth token for the app in `Authorization: Bearer …`. That's why the VirtuWill
token has its own header. A Claude cloud task also needs the app's host allowed
in its environment's network settings.

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

A prompt for a daily routine (with the token in its environment as
`VIRTUWILL_TOKEN` and the app's address as `VIRTUWILL_URL`):

> Each morning: read `$VIRTUWILL_URL/api/ingest/v1/finance/status` with header
> `X-VirtuWill-Token: $VIRTUWILL_TOKEN`. For each account, gather today's balance
> and every transaction since three days before its `last_transaction_on` from
> *[your source: bank export, aggregator, email statements]*. Include each
> transaction's id from the source as `external_id`, and mark unsettled ones
> `"pending": true`. POST them as one JSON body to
> `$VIRTUWILL_URL/api/ingest/v1/finance` with `"source": "claude-daily"`. If the
> response is 400, fix what `error` names and retry once. Report the counts from
> `report`.
