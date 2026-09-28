"""Plaid's shapes → VirtuWill's finance model. Standard library only.

Shared by the two ways Plaid data arrives:

- jobs/plaid_to_virtuwill.py pulls from Plaid and posts to the ingest API;
- virtuwill/plaid_mirror.py reads the lakehouse's Plaid tables once they are
  synced into Lakebase.

Both give a transaction the same external_id (a settled charge keeps the id of
the pending one it replaces), so the two never double a row. The job loads
this file by path, so nothing here may import from the virtuwill package.
"""
import json
from datetime import date, datetime

ACCOUNT_TYPES = {"credit": "credit_card", "loan": "loan", "other": "other"}
SAVINGS = {"savings", "money market", "cd", "hsa"}
RETIREMENT = {"401a", "401k", "403b", "457b", "ira", "roth", "roth 401k", "sep ira", "simple ira", "pension", "keogh",
              "retirement", "sarsep", "thrift savings plan", "lira", "rrsp", "tfsa"}

# Plaid's personal finance categories → the app's. Anything else goes to Review.
CATEGORIES = {"GENERAL_MERCHANDISE": "Shopping", "TRANSPORTATION": "Transportation", "TRAVEL": "Travel",
              "ENTERTAINMENT": "Entertainment", "RENT_AND_UTILITIES": "Bills & utilities", "PERSONAL_CARE": "Personal care",
              "MEDICAL": "Health", "GENERAL_SERVICES": "Services", "HOME_IMPROVEMENT": "Home",
              "GOVERNMENT_AND_NON_PROFIT": "Taxes & giving", "BANK_FEES": "Fees", "LOAN_PAYMENTS": "Loan payments"}
# Plaid's retirement subtypes → the plans the finance model knows (the rest count as a 401(k)).
RETIREMENT_TYPES = {"roth": "roth_ira", "ira": "traditional_ira", "sep ira": "traditional_ira", "simple ira": "traditional_ira",
                    "sarsep": "traditional_ira", "403b": "403b", "401a": "401a"}
PRIMARIES = set(CATEGORIES) | {"FOOD_AND_DRINK", "TRANSFER_IN", "TRANSFER_OUT", "INCOME"}


def account_type(a):
    kind, sub = str(a.get("type") or "").lower(), str(a.get("subtype") or "").lower()
    if kind == "depository":
        return "savings" if sub in SAVINGS else "checking"
    if kind == "investment":
        return "retirement" if sub in RETIREMENT or "ira" in sub or "401" in sub else "brokerage"
    return ACCOUNT_TYPES.get(kind, "other")


def personal_finance_category(value):
    """{primary, detailed} from Plaid's object, its JSON text, or one code ("FOOD_AND_DRINK" or
    "FOOD_AND_DRINK_GROCERIES"). Plaid's older category lists carry no code: {} (→ Review)."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                value = json.loads(text)
            except ValueError:
                return {}
        else:
            code = text.upper()
            if code in PRIMARIES:
                return {"primary": code, "detailed": ""}
            primary = max((p for p in PRIMARIES if code.startswith(p + "_")), key=len, default="")
            return {"primary": primary, "detailed": code} if primary else {}
    return value if isinstance(value, dict) else {}


def _pfc(t):
    pfc = t.get("personal_finance_category") or {}
    return str(pfc.get("primary") or "").upper(), str(pfc.get("detailed") or "").upper()


def kind_and_category(t):
    """(kind, category) for a Plaid transaction. Plaid's sign matches the app's: positive = money out."""
    primary, detailed = _pfc(t)
    amount = float(t.get("amount") or 0)
    if detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT":
        return "card_payment", None
    if primary in ("TRANSFER_IN", "TRANSFER_OUT"):
        return "transfer", None
    if primary == "INCOME":
        return "income", None
    if primary == "FOOD_AND_DRINK":
        category = "Groceries" if "GROCER" in detailed else "Dining"
    else:
        category = CATEGORIES.get(primary)
    return ("refund" if amount < 0 else "expense"), category


def day(value):
    """'YYYY-MM-DD' from a date, a datetime or text; None for nothing (Plaid's missing authorized_date)."""
    if value in (None, "", "None"):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def mask_of(a, overrides):
    m = str(a.get("mask") or "")
    return overrides.get(a.get("account_id")) or (m if len(m) == 4 and m.isdigit() else None)


def external_id(t):
    """A settled charge names the pending one it replaces: same id, so the app updates it in place."""
    return t.get("pending_transaction_id") or t.get("transaction_id")


def account_row(institution, a, overrides):
    """(the account for the ingest body, its mask) — or (None, why it was skipped)."""
    if str((a.get("balances") or {}).get("iso_currency_code") or "USD") != "USD":
        return None, f"{institution} {a.get('name')}: not in USD"
    mask = mask_of(a, overrides)
    if not mask:
        return None, (f"{institution} {a.get('name')} (account_id {a.get('account_id')}): no four-digit mask; "
                      "add it to PLAID_MASKS")
    row = {"mask": mask, "institution": institution, "name": a.get("official_name") or a.get("name") or f"Account {mask}",
           "account_type": account_type(a)}
    if row["account_type"] == "retirement":
        row["retirement_type"] = RETIREMENT_TYPES.get(str(a.get("subtype") or "").lower(), "401k")
    return row, mask


def balance_rows(a, mask, as_of):
    """The account's balances on as_of: what's owed or held, and for a bank account what's available."""
    bal, rows = a.get("balances") or {}, []
    if bal.get("current") is not None:
        rows.append({"account_mask": mask, "as_of": as_of, "balance": float(bal["current"]), "kind": "reported"})
    if bal.get("available") is not None and str(a.get("type")).lower() == "depository":
        rows.append({"account_mask": mask, "as_of": as_of, "balance": float(bal["available"]), "kind": "available"})
    return rows


def transaction_row(t, mask):
    kind, category = kind_and_category(t)
    posted = day(t.get("date"))
    row = {"account_mask": mask, "posted_on": posted, "transacted_on": day(t.get("authorized_date")) or posted,
           "description": t.get("merchant_name") or t.get("name") or "",
           "amount": round(float(t.get("amount") or 0), 2), "kind": kind,
           "external_id": external_id(t), "pending": bool(t.get("pending"))}
    if category:
        row["category"] = category
    return row


def bundle(institution, accounts, transactions, today, overrides=None):
    """The ingest API body for one institution's accounts and changed transactions.
    Returns (body, skipped) where skipped lists accounts with no usable four-digit mask."""
    overrides = overrides or {}
    body = {"source": "plaid", "accounts": [], "balances": [], "transactions": []}
    masks, skipped = {}, []
    for a in accounts:
        account, mask = account_row(institution, a, overrides)
        if not account:
            skipped.append(mask)
            continue
        masks[a["account_id"]] = mask
        body["accounts"].append(account)
        body["balances"] += balance_rows(a, mask, today)
    for t in transactions:
        mask = masks.get(t.get("account_id"))
        if mask:
            body["transactions"].append(transaction_row(t, mask))
    return body, skipped
