"""Push balances and transactions to VirtuWill from a scheduled job.

The app sits behind Databricks sign-in, so every call carries two tokens:
a Databricks OAuth token for a service principal that can use the app, and
the VirtuWill API token (Settings › API access). See docs/finance-api.md.

    python scripts/finance_feed.py status                  what's loaded (a safe first check)
    python scripts/finance_feed.py push day.json           load a JSON body (see docs/finance-api.md)
    python scripts/finance_feed.py push day.json --dry-run show what would change, load nothing

Settings come from the environment:

    VIRTUWILL_URL             the app's address, https://virtuwill-….databricksapps.com
    VIRTUWILL_TOKEN           vw_… from Settings › API access
    DATABRICKS_HOST           the workspace address, https://….cloud.databricks.com
    DATABRICKS_CLIENT_ID      the service principal's Application ID
    DATABRICKS_CLIENT_SECRET  its OAuth secret
    DATABRICKS_SCOPE          optional: the scope the secret was made with (tries 'apps', then 'all-apis')

Without DATABRICKS_HOST it calls the app directly (a local copy, for testing).
Standard library only, so it runs anywhere Python does.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60


def env(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"Set {name} (see the top of this file)")
    return value


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        host = urllib.parse.urlsplit(url).netloc
        sys.exit(f"Can't reach {host}: {e.reason}. Check the address, and that this machine's network allows it.")


def databricks_token():
    """A short-lived OAuth token for the service principal, or None when calling a local copy."""
    host = env("DATABRICKS_HOST", required=False).rstrip("/")
    if not host:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    basic = base64.b64encode(f"{env('DATABRICKS_CLIENT_ID')}:{env('DATABRICKS_CLIENT_SECRET')}".encode()).decode()
    scopes = [env("DATABRICKS_SCOPE", required=False)] if os.environ.get("DATABRICKS_SCOPE") else ["apps", "all-apis"]
    last = ""
    for scope in scopes:
        status, body = _request(f"{host}/oidc/v1/token",
                                data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode(),
                                headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        if status == 200:
            return json.loads(body)["access_token"]
        last = f"{status} {body[:300].decode(errors='replace')}"
    sys.exit(f"Databricks sign-in failed (scopes tried: {', '.join(scopes)}): {last}\n"
             "Check the client ID and secret, and set DATABRICKS_SCOPE to the scope the secret was made with.")


def call(path, body=None):
    url = env("VIRTUWILL_URL").rstrip("/") + path
    headers = {"X-VirtuWill-Token": env("VIRTUWILL_TOKEN"), "Accept": "application/json"}
    bearer = databricks_token()
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    status, raw = _request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        payload = json.loads(raw)
    except ValueError:
        # Databricks answers with a sign-in page, not JSON, when the service principal can't use the app.
        hint = (" The service principal needs 'Can use' on the app (app page › Share)."
                if status in (302, 401, 403) or b"<html" in raw[:200].lower() else "")
        sys.exit(f"{status} from {url}: not JSON.{hint}")
    return status, payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="accounts, latest balances and recent loads")
    push = sub.add_parser("push", help="load a JSON body of balances and/or transactions")
    push.add_argument("file", help="JSON file, or - for stdin")
    push.add_argument("--dry-run", action="store_true", help="show what would change without loading")
    args = parser.parse_args(argv)

    if args.command == "status":
        status, payload = call("/api/ingest/v1/finance/status")
    else:
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        body = json.loads(text)
        if args.dry_run:
            body["dry_run"] = True
        status, payload = call("/api/ingest/v1/finance", body)
    print(json.dumps(payload, indent=2, default=str))
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
