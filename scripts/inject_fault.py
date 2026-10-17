import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "app" / "config.py"

FAULTS = {
    "db_pool": {
        "old": "BATCH_SIZE = 100", "new": "BATCH_SIZE = 1000",
        "message": "Increase batch size from 100 to 1000",
    },
    "bad_config": {
        "old": 'DB_HOST = os.getenv("DB_HOST", "demo-db")', "new": 'DB_HOST = os.getenv("DB_HOST", "db-old")',
        "message": "Update config: point DB host to db-old",
    },
    "memory_leak": {
        "old": "ENABLE_RECEIPT_CACHE = False", "new": "ENABLE_RECEIPT_CACHE = True",
        "message": "Enable receipt caching for faster repeat lookups",
    },
}


def sh(*cmd, env=None, capture=False):
    r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, text=True, capture_output=capture)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr if capture else ''}")
    return r.stdout.strip() if capture else ""


def swap(old: str, new: str):
    lines = CONFIG.read_text().splitlines()
    hits = [i for i, line in enumerate(lines) if line.strip() == old]
    if len(hits) != 1:
        sys.exit(f"Expected exactly one line '{old}' in config.py. Already applied?")
    lines[hits[0]] = new
    CONFIG.write_text("\n".join(lines) + "\n")


def deploy():
    sha = sh("git", "rev-parse", "--short", "HEAD", capture=True)
    sh("docker", "compose", "up", "-d", "--build", "payment-service", env={"GIT_SHA": sha})
    print(f"Deployed {sha}")


def commit_and_deploy(message: str):
    sh("git", "add", "app/config.py")
    sh("git", "commit", "-m", message)
    if sh("git", "remote", capture=True):
        sh("git", "push")
    deploy()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "inject", "revert", "deploy"):
        sys.exit("usage: inject_fault.py list | inject NAME | revert NAME | deploy")
    cmd = sys.argv[1]
    if cmd == "list":
        for name, f in FAULTS.items():
            print(f"{name:12} {f['message']}")
        return
    if cmd == "deploy":
        deploy()
        return
    if len(sys.argv) < 3 or sys.argv[2] not in FAULTS:
        sys.exit("pick one of: " + ", ".join(FAULTS))
    if sh("git", "status", "--porcelain", capture=True):
        sys.exit("Working tree not clean. Commit or stash your changes first.")
    f = FAULTS[sys.argv[2]]
    if cmd == "inject":
        swap(f["old"], f["new"])
        commit_and_deploy(f["message"])
    else:
        swap(f["new"], f["old"])
        commit_and_deploy(f'Revert "{f["message"]}"')


if __name__ == "__main__":
    main()
