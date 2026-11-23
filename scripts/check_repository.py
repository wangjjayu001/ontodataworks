"""Check publishable files, local documentation links and module/config drift."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

import yaml
from sync_modules import ROOT, sync


def repository_files():
    output = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
    return sorted({p.decode("utf-8") for p in output.split(b"\0") if p and (ROOT / p.decode("utf-8")).is_file()})


def main():
    sync(check=True)
    paths = repository_files()
    errors = []
    modules = json.loads((ROOT / "project/modules.json").read_text(encoding="utf-8"))["modules"]
    if len({m["id"] for m in modules}) != len(modules):
        errors.append("Duplicate module ID")
    patterns = [p for m in modules for p in m["paths"] + m["tests"]]
    secret_patterns = [
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    ]
    checked_links = 0
    for name in paths:
        path = ROOT / name
        parts = path.relative_to(ROOT).parts
        forbidden = {"local", "backups", "scratch", "evidence", "node_modules", "dist", ".venv", "__pycache__"}
        if any(part in forbidden for part in parts) or path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            errors.append(f"Local/generated file in publication: {name}")
        if path.suffix in {".zip", ".dump", ".db", ".duckdb", ".pem", ".key", ".token"}:
            errors.append(f"Archive/database/credential file in publication: {name}")
        if path.stat().st_size > 25 * 1024 * 1024:
            errors.append(f"Unexpected large source file: {name}")
        if name.startswith(("backend/app/", "frontend/src/")) and path.suffix in {".py", ".js", ".jsx", ".css", ".json"}:
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
                errors.append(f"Source has no module: {name}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if not name.startswith("third-party-licenses/"):
            for pattern in secret_patterns:
                if pattern.search(content):
                    errors.append(f"Potential credential/private key: {name}")
        if path.suffix == ".json":
            try:
                json.loads(content)
            except ValueError as exc:
                errors.append(f"Invalid JSON {name}: {exc}")
        if name.startswith(".github/") and path.suffix in {".yml", ".yaml"}:
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as exc:
                errors.append(f"Invalid GitHub YAML {name}: {exc}")
        if path.suffix == ".md" and not name.startswith("third-party-licenses/"):
            for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", content):
                target = match.group(1).strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme in {"http", "https", "mailto"} or not parsed.path:
                    continue
                if parsed.scheme or target.startswith("/"):
                    errors.append(f"Nonportable documentation link {name}: {target}")
                    continue
                resolved = (path.parent / unquote(parsed.path)).resolve()
                if not resolved.is_relative_to(ROOT.resolve()) or not resolved.exists():
                    errors.append(f"Broken documentation link {name}: {target}")
                checked_links += 1
    if errors:
        raise SystemExit("\n".join(errors))
    print(json.dumps({"files": len(paths), "local_links": checked_links, "modules": len(modules), "result": "passed"}))


if __name__ == "__main__":
    main()
