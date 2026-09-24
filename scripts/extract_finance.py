"""Turn raw portal exports into the structured finance bundle.

    python scripts/extract_finance.py statement.pdf                    → statement.finance.json
    python scripts/extract_finance.py receipts.csv items.csv -o groceries.json
    python scripts/extract_finance.py report.pdf --csv out/            → out/transactions.csv, …

Recognised: Chase spending summary report (PDF), Chase card statement (PDF),
payroll earning statement report (PDF), grocery receipts and items CSVs.
Receipts and items CSVs given together are joined into one bundle.
Load the result in the workspace (Money › Imports) or with scripts/load_finance.py.
Only account last-four digits are kept; outputs contain personal data — keep them private.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from virtuwill.importers import ExtractError, extract, merge          # noqa: E402
from virtuwill.importers.canonical import to_csv                      # noqa: E402


def summary(bundle):
    counts = ", ".join(f"{len(bundle[s])} {s}" for s in ("transactions", "statements", "balances", "paychecks", "receipts")
                       if bundle.get(s))
    checks = bundle["document"].get("checks") or {}
    failed = [k for k, v in checks.items() if not v.get("ok", True)]
    return counts + (f"; checks failed: {', '.join(failed)}" if failed else "; all totals check out" if checks else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="JSON file to write (default: <first file>.finance.json)")
    parser.add_argument("--csv", type=Path, metavar="FOLDER", help="also write one CSV per record type here")
    args = parser.parse_args()
    try:
        bundle = merge([extract(p) for p in args.files])
    except (ExtractError, OSError) as error:
        parser.exit(1, f"error: {error}\n")
    out = args.output or args.files[0].with_suffix(".finance.json")
    out.write_text(json.dumps(bundle, indent=1, default=str), encoding="utf-8")
    print(f"{out}: {summary(bundle)}")
    if args.csv:
        for path in to_csv(bundle, args.csv):
            print(f"  {path}")


if __name__ == "__main__":
    main()
