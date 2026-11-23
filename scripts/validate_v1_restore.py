"""Restore ignored control-plane backups into isolated, newly-created databases."""
from runtime import ROOT, ARGS, compose, sql
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import uuid


def counts(database):
    tables = sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename", database).splitlines()
    return {table: int(sql('SELECT count(*) FROM public."' + table + '"', database)) for table in tables}


def main():
    folder = ROOT / "backups/v1.0-accepted"
    folder.mkdir(parents=True, exist_ok=True)
    current = folder / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".dump")
    with current.open("xb") as stream:
        process = subprocess.run(ARGS + ["exec", "-T", "db", "pg_dump", "-U", "workbench", "-Fc", "workbench"], stdout=stream, stderr=subprocess.PIPE)
    assert process.returncode == 0 and current.stat().st_size > 1000
    expected = counts("workbench")
    outcomes = []
    for path in (ROOT / "backups/v1.0-before/workbench.dump", current):
        target = "v1_restore_" + uuid.uuid4().hex[:16]
        sql("CREATE DATABASE " + target)
        try:
            with path.open("rb") as stream:
                restored = subprocess.run(ARGS + ["exec", "-T", "db", "pg_restore", "-U", "workbench", "-d", target, "--no-owner", "--exit-on-error"], stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert restored.returncode == 0, restored.stderr.decode(errors="replace")[:500]
            actual = counts(target)
            if path == current:
                assert actual == expected, "Restored control-plane row counts differ"
                assert sql("SELECT max(version) FROM wb_schema_version", target) == "4"
            else:
                assert sql("SELECT max(version) FROM wb_schema_version", target) == "3"
            outcomes.append({"backup": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size, "restored": True, "table_row_counts": actual, "all_current_counts_match": path == current})
        finally:
            sql("DROP DATABASE " + target)
    report = {"passed": True, "checked_at": datetime.now(timezone.utc).isoformat(), "scope": "isolated control DB restores; no warehouse data changed", "restores": outcomes}
    report_path = ROOT / "evidence/v1.0/restore-acceptance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "restores": len(outcomes), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
