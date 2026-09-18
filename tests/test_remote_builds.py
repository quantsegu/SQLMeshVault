"""Offline native-model compilation for the three warehouse backends."""

from pathlib import Path

import pytest
from sqlglot import exp
from sqlmesh.core.dialect import parse
from sqlmesh_vault.remote import build
from sqlmesh_vault.warehouse import Connection

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_remote_vault_models_parse_without_connecting(engine, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline compile attempted a connection")

    monkeypatch.setattr(Connection, "connect", forbidden)
    build(
        ROOT / "vendor/HamiltonVault/examples/replacement/project.json",
        ROOT / "examples/warehouses" / f"{engine}-vault.json",
        tmp_path / "project",
    )
    model_files = list((tmp_path / "project/models").glob("*.sql"))
    assert len(model_files) == 17
    for file in model_files:
        parsed = parse(file.read_text(), default_dialect=engine)
        assert len(parsed) == 2
        if ".sat_customer.sql" in file.name:
            query = parsed[1]
            assert len(list(query.find_all(exp.CTE))) == 3, (
                "Satellite WITH clauses were lost"
            )
            assert any(t.alias == "s" for t in query.find_all(exp.Table)), (
                "Qualified source alias was lost"
            )
    for audit in (tmp_path / "project/audits").glob("*.sql"):
        assert len(parse(audit.read_text(), default_dialect=engine)) == 2
    assert not (tmp_path / "project/sources").exists(), (
        "Warehouse build must not ingest local CSVs"
    )
