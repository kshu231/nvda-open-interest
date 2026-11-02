#!/usr/bin/env python3
"""Sign in to Schwab and hand the login to the live gamma exposure service.

Run it in a terminal on this computer about once a week: Schwab's login lasts
7 days, and the page warns on the last day. It opens Schwab's login in the
browser, then saves the login to Google Secret Manager, where the service reads
it. The first run also asks for the app key and secret (see schwab_api.py for
where this computer keeps them). Nothing is printed but the outcome.

Needs the gcloud CLI, signed in to the account that owns PROJECT.

Exit codes: 0 saved, 1 could not sign in or save.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import schwab_api
from schwab_api import SchwabError

PROJECT = "nvda-open-interest"
SECRET = "schwab-credentials"
# What the service needs; the access token it makes for itself.
KEPT = ("app_key", "app_secret", "callback", "refresh_token", "logged_in_at")


def gcloud() -> str:
    found = shutil.which("gcloud") or str(Path.home() / "google-cloud-sdk" / "bin" / "gcloud")
    if not os.access(found, os.X_OK):
        raise SchwabError("the gcloud CLI is not installed")
    return found


def save_to_cloud(store: dict) -> None:
    payload = json.dumps({k: store[k] for k in KEPT})
    base = [gcloud(), "secrets", "--project", PROJECT, "--quiet"]
    exists = subprocess.run(base + ["describe", SECRET], capture_output=True).returncode == 0
    if not exists:
        subprocess.run(base + ["create", SECRET, "--replication-policy", "automatic"],
                       capture_output=True, check=True)
    # The login goes in on stdin, so it never appears in a command line or a log.
    added = subprocess.run(base + ["versions", "add", SECRET, "--data-file=-"],
                           input=payload.encode(), capture_output=True)
    if added.returncode:
        raise SchwabError("gcloud could not save the login: "
                          + added.stderr.decode("utf-8", "replace").strip()[-300:])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--no-login", action="store_true",
                   help="save this computer's current login without signing in again")
    args = p.parse_args(argv)
    store = schwab_api.load_store()
    try:
        if not args.no_login or not store.get("refresh_token"):
            schwab_api.log_in(store)
        save_to_cloud(store)
    except (SchwabError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Login saved. The live chart picks it up within 5 minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
