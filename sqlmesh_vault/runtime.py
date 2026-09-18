from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
from sqlmesh import Context as SQLMeshContext
from vault.v2.engine import Context as VaultContext
from vault.v2.engine import read_project, run_checks, timestamp
from vault.v2.sql import model_plan, q, quality_checks


def sync_sources(path: str | Path, execution_time: str) -> None:
    """Validate on an isolated copy before publishing immutable raw event histories.

    SQLMesh transactions are model-scoped. This preflight prevents known invalid
    source batches before any source table or model is changed.
    """
    path = Path(path).resolve()
    project = read_project(path / "vault_project.json")
    sources = json.loads((path / "sources.json").read_text())
    with (
        duckdb.connect(str(path / "warehouse.duckdb")) as target,
        duckdb.connect() as scratch,
    ):
        scratch.execute("SET TimeZone='UTC'")
        vc = VaultContext(project, scratch, path, timestamp(execution_time))
        for name in project.stages:
            vc.stage(name)
        known = {
            tuple(r)
            for r in target.execute(
                "SELECT table_schema,table_name FROM information_schema.tables"
            ).fetchall()
        }
        raw_rows = {}
        for name, spec in sources.items():
            with (path / spec["path"]).open(newline="") as f:
                reader = csv.DictReader(f)
                raw_rows[name] = [
                    tuple(value or None for value in row.values()) for row in reader
                ]
            if ("raw", name) in known:
                old = Counter(target.execute(f"SELECT * FROM raw.{q(name)}").fetchall())
                if old - Counter(raw_rows[name]):
                    raise ValueError(
                        f"{name}: source history cannot delete or modify previously ingested events"
                    )
        for m in project.models:
            scratch.execute(model_plan(project, m).ddl)
            if ("vault", m.name) in known:
                frame = target.execute(f"SELECT * FROM vault.{q(m.name)}").fetchdf()
                scratch.register("_existing", frame)
                scratch.execute(f"INSERT INTO {q(m.name)} SELECT * FROM _existing")
                scratch.unregister("_existing")
        pending = {m.name: m for m in project.models}
        done = set()
        while pending:
            ready = [m for m in pending.values() if set(m.dependencies()) <= done]
            if not ready:
                raise ValueError("Cyclic model graph")
            for m in ready:
                vc.load(m.name)
                done.add(m.name)
                del pending[m.name]
        run_checks(scratch, quality_checks(project))
        target.execute("BEGIN")
        try:
            target.execute("CREATE SCHEMA IF NOT EXISTS raw")
            for name, spec in sources.items():
                definitions = ", ".join(q(c) + " VARCHAR" for c in spec["columns"])
                target.execute(f"CREATE OR REPLACE TABLE raw.{q(name)} ({definitions})")
                if raw_rows[name]:
                    target.executemany(
                        f"INSERT INTO raw.{q(name)} VALUES ({','.join('?' for _ in spec['columns'])})",
                        raw_rows[name],
                    )
            target.execute("COMMIT")
        except Exception:
            target.execute("ROLLBACK")
            raise


def apply(path: str | Path, execution_time: str = "2026-01-05T00:00:00Z") -> dict:
    path = Path(path).resolve()
    if (path / "warehouse-plan.json").exists():
        from .remote import apply as apply_remote

        return apply_remote(path, execution_time)
    state_path = path / "load_state.json"
    source_hash = hashlib.sha256(
        b"".join(f.read_bytes() for f in sorted((path / "sources").glob("*.csv")))
    ).hexdigest()
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if (
            source_hash != state["source_hash"]
            and datetime.fromisoformat(timestamp(execution_time)).date()
            <= datetime.fromisoformat(state["execution_time"]).date()
        ):
            raise ValueError("Changed sources require a later daily execution interval")
    sync_sources(path, execution_time)
    context = SQLMeshContext(paths=path)
    try:
        tests = context.test()
        if not tests.wasSuccessful():
            raise ValueError("SQLMesh model tests failed")
        context.plan(
            "prod", auto_apply=True, no_prompts=True, execution_time=execution_time
        )
        result = context.run(
            "prod",
            start="2026-01-01",
            end=execution_time,
            execution_time=execution_time,
            ignore_cron=True,
        )
        if result.is_failure:
            raise RuntimeError("SQLMesh run failed")
        project = read_project(path / "vault_project.json")
        counts = {
            m.name: int(
                context.fetchdf(f"SELECT COUNT(*) AS n FROM vault.{q(m.name)}").iloc[
                    0, 0
                ]
            )
            for m in project.models
        }
        state_path.write_text(
            json.dumps(
                {
                    "source_hash": source_hash,
                    "execution_time": timestamp(execution_time),
                }
            )
        )
        return {
            "status": "success",
            "engine": "sqlmesh",
            "counts": counts,
            "model_tests": tests.testsRun,
        }
    finally:
        context.close()
