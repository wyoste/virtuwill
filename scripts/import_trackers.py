"""One-time private import on the deployment host; never commit the inputs.

Needs the same PG* settings as the app (a Lakebase database, or a local
PostgreSQL for development).
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: F401  loads the same .env as the Flask app
from virtuwill import trackers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finance", type=Path)
    parser.add_argument("--health", type=Path)
    args = parser.parse_args()
    if not args.finance and not args.health:
        parser.error("Supply --finance and/or --health with the original HTML file path.")
    for kind in ("finance", "health"):
        path = getattr(args, kind)
        if path:
            try:
                trackers.install(kind, path.read_text(encoding="utf-8"))
            except (ValueError, trackers.AlreadyInstalled) as error:
                parser.exit(1, f"{kind}: not imported ({error}). Existing records were preserved.\n")
            print(f"{kind}: imported into the private database")


if __name__ == "__main__":
    main()
