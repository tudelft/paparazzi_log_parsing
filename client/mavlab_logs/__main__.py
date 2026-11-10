"""Command line: mavlab-logs login | logout | status | download | upload."""
import argparse
import sys

from . import __version__, settings
from .client import Client, Error


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mavlab-logs", description="Download and upload logs of a flight log database.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--server", help="server URL (default: LOGDB_SERVER or the last server signed in to)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the https certificate")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("login", help="sign in through the browser (or store an API key with --key)")
    p.add_argument("--key", help="API key from the account page, stored instead of a browser login")
    p.add_argument("--no-browser", action="store_true", help="only print the link")
    sub.add_parser("logout", help="revoke and forget the stored key")
    sub.add_parser("status", help="show the server, user, key expiry and cache folder")
    p = sub.add_parser("download", help="download flights by number and print their local paths")
    p.add_argument("flights", nargs="+", type=int, help="flight numbers (the number in /flight/<number>)")
    p.add_argument("--mat", action="store_true", help="the flight's MATLAB export instead of the original log")
    p.add_argument("--force", action="store_true", help="download again even when cached")
    p = sub.add_parser("upload", help="upload log files")
    p.add_argument("files", nargs="+", help="log files (.data with its .log next to it, .BFL, .ulg, .bin, ...)")
    p.add_argument("--name", help="stored name of a single file, e.g. 'Hex 1/LOG00030.BFL'")
    p.add_argument("--wait", action="store_true", help="wait until the server has parsed each log")
    p.add_argument("--again", action="store_true", help="upload even when the server already has the log")
    a = ap.parse_args(argv)
    try:
        client = Client(a.server, insecure=a.insecure, login=a.command not in ("login", "logout", "status"))
        if a.command == "login":
            user = client.use_key(a.key) if a.key else client.login(open_browser=not a.no_browser)
            settings.set_default_server(client.server)
            print(f"Signed in to {client.server} as {user.get('display_name')}")
        elif a.command == "logout":
            client.logout()
            print(f"Signed out of {client.server}")
        elif a.command == "status":
            stored = settings.entry(client.server) or {}
            print(f"Server: {client.server}")
            print(f"Key: {client.key_source or 'none'}" + (f", until {stored['expires_at']}"
                  if client.key_source == "stored" and stored.get("expires_at") else ""))
            if client.key:
                print(f"User: {client.me().get('display_name')}")
            print(f"Cache: {settings.cache_dir()}")
        elif a.command == "download":
            for path in client.download(a.flights, mat=a.mat, force=a.force):
                print(path)
        elif a.command == "upload":
            for r in client.upload(a.files, name=a.name, wait=a.wait, skip_existing=not a.again):
                extra = f" log {r['log_id']}, flights {r.get('flight_ids')}" if r.get("log_id") else ""
                print(f"{r.get('name')}: {r['status']}{extra}")
    except (Error, OSError, ValueError) as exc:
        print(f"mavlab-logs: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
