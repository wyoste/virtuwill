"""One-time private import on the deployment host; never commit the inputs."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # loads the same .env as the Flask app
import storage
from trackers import TrackerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finance", type=Path)
    parser.add_argument("--health", type=Path)
    args = parser.parse_args()
    if not args.finance and not args.health:
        parser.error("Supply --finance and/or --health with the original HTML file path.")
    backend = storage.backend()
    store = TrackerStore(backend if backend.name == "lakebase" else os.environ.get("TRACKER_DATA_DIR", ROOT / "data/private-trackers"))
    for kind in ("finance", "health"):
        path = getattr(args, kind)
        if path:
            try:
                store.install(kind, path.read_text(encoding="utf-8"))
            except (ValueError, storage.AlreadyInstalled) as error:
                parser.exit(1, f"{kind}: not imported ({error}). Existing records were preserved.\n")
            print(f"{kind}: imported into private {backend.name} storage")


if __name__ == "__main__":
    main()
