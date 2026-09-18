"""Portable stage/hub/link/satellite plans over existing warehouse landing tables."""

from __future__ import annotations

import json
from pathlib import Path

import sqlglot
from sqlglot import exp
from vault.v2.engine import Context, make_driver, read_project, timestamp
from vault.v2.sql import model_plan, quality_checks, stage_checks, stage_sql, types_for

from .warehouse import Connection, relation, render, table, validate


def qualify(sql, mapping):
    tree = sqlglot.parse_one(sql, read="duckdb")
    for item in list(tree.find_all(exp.Table)):
        if not item.db and item.name in mapping:
            replacement = table(mapping[item.name])
            if item.args.get("alias"):
                replacement.set("alias", item.args["alias"].copy())
            item.replace(replacement)
    return tree.sql(dialect="duckdb")


def data_type(dtype, engine, nullable=False):
    if engine == "clickhouse":
        value = {
            "VARCHAR": "String",
            "BLOB": "String",
            "TIMESTAMPTZ": "DateTime64(6, 'UTC')",
        }.get(dtype, dtype)
        return f"Nullable({value})" if nullable else value
    return exp.DataType.build(dtype, dialect="duckdb").sql(dialect=engine)


def compile_project(metadata, connection_config, output):
    project = read_project(metadata)
    config = json.loads(Path(connection_config).read_text())
    if set(config) != {
        "warehouse",
        "source_tables",
        "target_schema",
        "staging_schema",
        "load_dts",
    }:
        raise ValueError(
            "Expected warehouse, source_tables, target_schema, staging_schema, load_dts"
        )
    warehouse = validate(config["warehouse"])
    engine = warehouse["type"]
    if set(config["source_tables"]) != set(project.stages):
        raise ValueError("Each metadata stage needs a warehouse source table")
    for value in config["source_tables"].values():
        relation(value, engine)
    for key in ["target_schema", "staging_schema"]:
        relation(config[key], engine, schema=True)
    if config["target_schema"].lower() == config["staging_schema"].lower():
        raise ValueError("Use separate target and staging schemas")
    loaded = timestamp(config["load_dts"])
    output = Path(output)
    if output.exists():
        raise ValueError("Output already exists")
    mapping = {m.name: config["target_schema"] + "." + m.name for m in project.models}
    for stage in project.stages:
        mapping["_hv_stage_" + stage] = (
            config["staging_schema"] + "." + project.name + "_stage_" + stage
        )
    for m in project.models:
        for prefix in ["_hv_raw_", "_hv_input_"]:
            mapping[prefix + m.name] = (
                config["staging_schema"] + "." + project.name + prefix + m.name
            )
    if {v.lower() for v in mapping.values()} & {
        v.lower() for v in config["source_tables"].values()
    }:
        raise ValueError("Source tables cannot overlap generated relations")
    result = {
        "warehouse": warehouse,
        "config": config,
        "stages": {},
        "models": {},
        "quality": [],
        "metadata": project.model_dump(),
        "validation": "offline generated capability; no live warehouse certification",
    }
    for stage, spec in project.stages.items():
        sql = stage_sql(project, stage, "unused.csv", loaded).split(" AS\n", 1)[1]
        raw = (
            "SELECT "
            + ", ".join(f'CAST("{c}" AS VARCHAR) AS "{c}"' for c in spec.columns)
            + " FROM "
            + table(config["source_tables"][stage]).sql()
        )
        sql = (
            "WITH raw AS (" + raw + "), derived AS" + sql.split("),\nderived AS", 1)[1]
        )
        result["stages"][stage] = {
            "relation": mapping["_hv_stage_" + stage],
            "query": render(sql, engine),
            "checks": [
                {"name": c.name, "sql": render(qualify(c.sql, mapping), engine)}
                for c in stage_checks(project, stage)
            ],
        }
    for m in project.models:
        plan = model_plan(project, m)
        raw, candidate = sqlglot.parse(plan.prepare, read="duckdb")
        prepares = []
        for expression, prefix in [(raw, "_hv_raw_"), (candidate, "_hv_input_")]:
            prepares.append(
                {
                    "relation": mapping[prefix + m.name],
                    "query": render(
                        qualify(expression.expression.sql(dialect="duckdb"), mapping),
                        engine,
                    ),
                }
            )
        required = {
            m.src_pk,
            m.src_ldts,
            m.src_source,
            *m.src_fk,
            *([m.src_hashdiff] if m.src_hashdiff else []),
        }
        columns = ", ".join(
            f"{exp.to_identifier(c, quoted=True).sql(dialect=engine)} {data_type(t, engine, c not in required)}"
            + (" NOT NULL" if c in required and engine != "clickhouse" else "")
            for c, t in types_for(project, m).items()
        )
        keys = [m.src_pk, m.src_ldts] if m.kind == "sat" else [m.src_pk]
        ddl = (
            "CREATE TABLE IF NOT EXISTS "
            + table(mapping[m.name]).sql(dialect=engine)
            + " ("
            + columns
            + ")"
        )
        if engine == "clickhouse":
            ddl += (
                " ENGINE = MergeTree ORDER BY ("
                + ", ".join(
                    exp.to_identifier(c, quoted=True).sql(dialect=engine) for c in keys
                )
                + ")"
            )
        checks = []
        for c in plan.checks:
            sql = c.sql
            if "late-arriving change" in c.name:
                pk, ldts, hd = (
                    '"' + x + '"' for x in [m.src_pk, m.src_ldts, m.src_hashdiff]
                )
                sql = f'''SELECT 1 FROM "_hv_input_{m.name}" s
JOIN (SELECT {pk}, MAX({ldts}) AS newest FROM "{m.name}" GROUP BY {pk}) n ON s.{pk}=n.{pk}
LEFT JOIN "{m.name}" t ON s.{pk}=t.{pk} AND t.{ldts}<=s.{ldts}
WHERE s.{ldts}<=n.newest
QUALIFY ROW_NUMBER() OVER(PARTITION BY s.{pk},s.{ldts},s.{hd} ORDER BY t.{ldts} DESC)=1
AND s.{hd} IS DISTINCT FROM t.{hd}'''
            checks.append(
                {"name": c.name, "sql": render(qualify(sql, mapping), engine)}
            )
        insert = render(qualify(plan.insert, mapping), engine)
        canonical = sqlglot.parse_one(qualify(plan.insert, mapping), read="duckdb")
        selection = canonical.expression.copy()
        if canonical.args.get("with_"):
            selection.set("with_", canonical.args["with_"].copy())
        select = render(selection.sql(dialect="duckdb"), engine)
        result["models"][m.name] = {
            "relation": mapping[m.name],
            "ddl": ddl,
            "prepare": prepares,
            "checks": checks,
            "insert": insert,
            "query": select,
            "dependencies": m.dependencies(),
            "columns": types_for(project, m),
            "keys": keys,
            "required": sorted(required),
        }
    result["quality"] = [
        {"name": c.name, "sql": render(qualify(c.sql, mapping), engine)}
        for c in quality_checks(project)
    ]
    output.mkdir(parents=True)
    (output / "warehouse-plan.json").write_text(json.dumps(result, indent=2))
    return result


class WarehouseContext(Context):
    def __init__(self, plan, connection):
        self.plan, self.connection = plan, connection

    def checks(self, checks):
        for check in checks:
            if self.connection.query(check["sql"])[1]:
                raise ValueError(check["name"])

    def stage(self, name):
        spec = self.plan["stages"][name]
        self.connection.execute(
            "CREATE OR REPLACE VIEW "
            + table(spec["relation"]).sql(dialect=self.connection.engine)
            + " AS "
            + spec["query"]
        )
        self.checks(spec["checks"])
        return {"stage": name}

    def load(self, name):
        spec = self.plan["models"][name]
        self.connection.execute(spec["ddl"])
        for prepared in spec["prepare"]:
            self.connection.execute(
                "CREATE OR REPLACE VIEW "
                + table(prepared["relation"]).sql(dialect=self.connection.engine)
                + " AS "
                + prepared["query"]
            )
        self.checks(spec["checks"])
        self.connection.execute(spec["insert"])
        return {"table": spec["relation"]}


def run(output):
    from vault.v2.metadata import Project

    plan = json.loads((Path(output) / "warehouse-plan.json").read_text())
    project = Project.model_validate(plan["metadata"])
    connection = Connection(plan["warehouse"])
    context = WarehouseContext(plan, connection)
    try:
        for key in ["target_schema", "staging_schema"]:
            schema = table(plan["config"][key]).sql(dialect=connection.engine)
            connection.execute(
                (
                    "CREATE DATABASE IF NOT EXISTS "
                    if connection.engine == "clickhouse"
                    else "CREATE SCHEMA IF NOT EXISTS "
                )
                + schema
            )
        results = make_driver(project).execute(
            ["load_" + m.name for m in project.models], inputs={"context": context}
        )
        context.checks(plan["quality"])
        return {
            "status": "success",
            "engine": connection.engine,
            "models": results,
            "atomic_batch": False,
        }
    finally:
        connection.close()
