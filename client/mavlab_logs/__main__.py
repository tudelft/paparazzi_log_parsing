"""Command line: mavlab-logs [--server URL] [--key KEY] status | logbook | fetch | upload | revoke-all."""
import argparse
import sys

from . import __version__, settings
from .client import Client, Error


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mavlab-logs", description="Download and upload logs of a flight log database.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--server", required=True, help="server URL or host name")
    ap.add_argument("--key", help="API key (default: LOGDB_API_KEY, else the stored browser login, else a new one)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the https certificate")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="sign in if needed and show the server, user, key and cache folder")
    p = sub.add_parser("logbook", help="list the flights you may see, newest first, tab-separated")
    p.add_argument("--all", action="store_true", help="also flights judged uncertain or ground")
    p = sub.add_parser("fetch", help="download the original logs of flights by number and print their paths")
    p.add_argument("flights", nargs="+", type=int, help="flight numbers (the number in /flight/<number>)")
    p.add_argument("--scope", choices=("selected", "all", "log", "flight"), default="selected",
                   help="one path per log (selected, all, log: the same whole logs) or per flight number")
    p.add_argument("--force", action="store_true", help="download again even when cached")
    p = sub.add_parser("upload", help="upload log files")
    p.add_argument("files", nargs="+", help="log files (.data with its .log next to it, .BFL, .ulg, .bin, ...)")
    p.add_argument("--name", help="stored name of a single file, e.g. 'Hex 1/LOG00030.BFL'")
    p.add_argument("--wait", action="store_true", help="wait until the server has parsed each log")
    p.add_argument("--again", action="store_true", help="upload even when the server already has the log")
    p = sub.add_parser("revoke-all", help="revoke your keys from browser logins (all computers)")
    p.add_argument("--include-manual", action="store_true", help="also the keys made on the account page")
    a = ap.parse_args(argv)
    try:
        client = Client(a.server, a.key, insecure=a.insecure)
        if a.command == "status":
            stored = settings.entry(client.server) or {}
            user = client.me()
            print(f"Server: {client.server}")
            print(f"User: {user.get('display_name')}")
            print(f"Key: {client.key_source}" + (f", until {stored['expires_at']}"
                  if client.key_source == "browser login" and stored.get("expires_at") else ""))
            print(f"Cache: {settings.cache_dir()}")
        elif a.command == "logbook":
            columns = ("flight", "date", "aircraft", "duration_min", "pic", "project", "goal", "verdict", "log")
            print("\t".join(columns))
            for r in client.logbook(all=a.all):
                r["date"] = r["date"].strftime("%Y-%m-%d %H:%M:%S") if r["date"] else ""
                r["duration_min"] = f"{r['duration_min']:.1f}" if r["duration_min"] is not None else ""
                print("\t".join("" if r[c] is None else str(r[c]).replace("\t", " ") for c in columns))
        elif a.command == "fetch":
            for path in client.fetch(a.flights, scope=a.scope, force=a.force):
                print(path)
        elif a.command == "upload":
            for r in client.upload(a.files, name=a.name, wait=a.wait, skip_existing=not a.again):
                extra = f" log {r['log_id']}, flights {r.get('flight_ids')}" if r.get("log_id") else ""
                print(f"{r.get('name')}: {r['status']}{extra}")
        elif a.command == "revoke-all":
            client.revoke_all(a.include_manual)
    except (Error, OSError, ValueError) as exc:
        print(f"mavlab-logs: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
