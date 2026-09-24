"""Load finance data into Lakebase from the command line.

    python scripts/load_finance.py bundle.finance.json            stage and commit
    python scripts/load_finance.py statement.pdf --dry-run        show what would change
    python scripts/load_finance.py receipts.csv items.csv         raw exports are extracted first

Uses the same PG* settings as the app. The same checks and matching rules as
Money › Imports apply, and loading a file twice changes nothing.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402,F401  loads .env
from virtuwill import db  # noqa: E402
from virtuwill.importers import ExtractError, extract, merge  # noqa: E402
from virtuwill.importers import load as loader  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="show the preview and change nothing")
    args = parser.parse_args()
    try:
        bundle = merge([extract(p) for p in args.files])
    except (ExtractError, OSError) as error:
        parser.exit(1, f"error: {error}\n")
    with db.tx() as conn:
        if args.dry_run:
            print(json.dumps(loader.preview(conn, bundle), indent=1, default=str))
            conn.rollback()
            return
        import_id, report = loader.load(conn, bundle)
    print(f"import #{import_id}: {json.dumps(report, default=str)}")


if __name__ == "__main__":
    main()
