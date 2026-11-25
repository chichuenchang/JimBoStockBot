"""Deploy the JBSB mirror socket (../jbsb-miaoda): push sprint/default, release, check it shipped.

    uv run python scripts/deploy_miaoda.py

Dirty repo: stop and show it (commit yourself; its .env is tracked, so never `add -A` blind).

Port of BRecruitHelper scripts/deploy/miaoda-deploy.mjs, minus its build step (the socket is plain TS).
A release cut right after a push can snapshot the parent commit: wait out the mirror, compare, release again.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2] / "jbsb-miaoda"
BRANCH = "sprint/default"
REASON = "JBSB mirror update"


def run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def cli(app_id: str, *args: str) -> dict:
    out = run(shutil.which("lark-cli") or "lark-cli", "--profile", "brecruit", "apps", *args, "--as", "user",
              "--app-id", app_id)
    return json.loads(out).get("data") or {}


def release(app_id: str) -> dict:
    time.sleep(15)
    rid = cli(app_id, "+release-create", "--branch", BRANCH, "--apply-reason", REASON).get("release_id")
    if not rid:
        sys.exit("release-create returned no release_id")
    print(f"release {rid}: publishing")
    for _ in range(30):
        got = cli(app_id, "+release-get", "--release-id", rid)
        r = got.get("release", got)
        if r.get("status") == "finished":
            return {"id": rid, "commit": r.get("commit_id"), "url": r.get("online_url")}
        if r.get("status") == "failed":
            sys.exit(f"release {rid} failed: {json.dumps(r.get('error_logs') or r)[:3000]}")
        time.sleep(20)
    sys.exit(f"release {rid} still publishing after 10 min")


def main() -> None:
    if not REPO.exists():
        sys.exit(f"Miaoda repo missing: {REPO}")
    app_id = json.loads((REPO / ".spark" / "meta.json").read_text(encoding="utf-8"))["app_id"]
    if dirty := run("git", "status", "--porcelain"):
        sys.exit(f"{REPO} has uncommitted changes, commit them first:\n{dirty}")
    run("git", "push", "-q", "origin", BRANCH)
    pushed = run("git", "rev-parse", "HEAD")
    shipped = release(app_id)
    if shipped["commit"] and shipped["commit"] != pushed:
        print(f"release {shipped['id']} shipped {shipped['commit'][:7]}, not {pushed[:7]}: releasing again")
        shipped = release(app_id)
    if shipped["commit"] != pushed:
        sys.exit(f"release {shipped['id']} shipped {str(shipped['commit'])[:7]}, not {pushed[:7]}: stop, look")
    print(f"released: {shipped['id']}, commit {pushed[:7]}, {shipped['url']}")


if __name__ == "__main__":
    main()
