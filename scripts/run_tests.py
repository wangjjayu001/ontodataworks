"""Run existing offline unittest files in isolated processes; never run validate_*."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent


def main():
    tests = sorted((ROOT / "scripts").glob("test_*.py"))
    env = os.environ.copy()
    env.update(DATABASE_URL="sqlite://", ENCRYPTION_KEY=Fernet.generate_key().decode(), PYTHONIOENCODING="utf-8")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "scripts")])
    failed = []
    for test in tests:
        print(f"Running {test.name}", flush=True)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", test.name], cwd=ROOT, env=env)
        if result.returncode:
            failed.append(test.name)
    if failed:
        raise SystemExit("Failed suites: " + ", ".join(failed))
    print(f"All {len(tests)} offline suites passed.")


if __name__ == "__main__":
    main()
