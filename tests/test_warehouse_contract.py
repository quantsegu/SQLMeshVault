"""Offline adapter contracts: no remote connector or warehouse is contacted."""

import json
from pathlib import Path

import pytest
import sqlglot
from sqlmesh_vault.warehouse import Connection, options, relation, render, validate

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_compile_connection_is_lazy(engine, monkeypatch):
    spec = json.loads((ROOT / "examples/warehouses" / f"{engine}.json").read_text())
    for value in spec["connection"].values():
        if isinstance(value, dict):
            monkeypatch.delenv(value["env"], raising=False)
    assert Connection(spec).handle is None
    with pytest.raises(ValueError, match="Missing environment"):
        options(spec)


def test_no_literal_credentials():
    with pytest.raises(ValueError, match="environment reference"):
        validate({"type": "snowflake", "connection": {"password": "not-a-real-secret"}})


def test_env_resolution_types(monkeypatch):
    monkeypatch.setenv("CAP_PORT", "8443")
    monkeypatch.setenv("CAP_TLS", "false")
    assert options(
        {
            "type": "clickhouse",
            "connection": {"port": {"env": "CAP_PORT"}, "secure": {"env": "CAP_TLS"}},
        }
    ) == {"port": 8443, "secure": False}


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_binary_hash_and_nullsafe_rendering(engine):
    sql = render(
        "SELECT FROM_HEX(SHA256(x)) AS hk, a IS DISTINCT FROM b FROM source", engine
    )
    sqlglot.parse_one(sql, read=engine)
    assert "FROM_HEX" not in sql
    if engine == "snowflake":
        assert "HEX_DECODE_BINARY" in sql
    if engine == "clickhouse":
        assert "LOWER(HEX(SHA256" in sql.upper()


def test_clickhouse_database_table_only():
    assert relation("vault.orders", "clickhouse") == "vault.orders"
    with pytest.raises(ValueError):
        relation("catalog.vault.orders", "clickhouse")


@pytest.mark.parametrize("engine", ["databricks", "snowflake", "clickhouse"])
def test_driver_dispatch_with_in_memory_stubs(engine, monkeypatch):
    """Check real adapter argument dispatch without importing a network driver."""
    import sys
    import types

    captured = {}

    class Cursor:
        description = (("value",),)

        def execute(self, sql):
            captured["query"] = sql

        def fetchall(self):
            return [(7,)]

        def close(self):
            pass

    class Handle:
        def cursor(self):
            return Cursor()

        def close(self):
            captured["closed"] = True

        def query(self, sql):
            captured["query"] = sql
            return types.SimpleNamespace(column_names=["value"], result_rows=[(7,)])

    def connect(**kwargs):
        captured["options"] = kwargs
        return Handle()

    if engine == "databricks":
        module = types.ModuleType("databricks")
        module.sql = types.SimpleNamespace(connect=connect)
        monkeypatch.setitem(sys.modules, "databricks", module)
    elif engine == "snowflake":
        module = types.ModuleType("snowflake")
        module.connector = types.ModuleType("snowflake.connector")
        module.connector.connect = connect
        monkeypatch.setitem(sys.modules, "snowflake", module)
        monkeypatch.setitem(sys.modules, "snowflake.connector", module.connector)
    else:
        module = types.ModuleType("clickhouse_connect")
        module.get_client = connect
        monkeypatch.setitem(sys.modules, "clickhouse_connect", module)
    client = Connection({"type": engine, "connection": {}})
    assert client.query("SELECT 7 AS value") == (["value"], [(7,)])
    client.close()
    assert captured["closed"]
    if engine == "clickhouse":
        assert captured["options"]["settings"]["join_use_nulls"] == 1
    if engine == "databricks":
        assert (
            captured["options"]["session_configuration"]["spark.sql.session.timeZone"]
            == "UTC"
        )
    if engine == "snowflake":
        assert captured["options"]["session_parameters"]["TIMEZONE"] == "UTC"
