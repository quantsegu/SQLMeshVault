from __future__ import annotations

import json
import shutil
from pathlib import Path

import sqlglot
from vault.v2.engine import read_project
from vault.v2.sql import model_plan, q, stage_sql, types_for


def build(project_path: str | Path, output: str | Path) -> dict:
    """Generate native SQLMesh stages and INCREMENTAL_UNMANAGED vault models."""
    project_path, output = Path(project_path).resolve(), Path(output).resolve()
    project = read_project(project_path)
    output.mkdir(parents=True, exist_ok=True)
    for directory in ["models", "audits", "tests", "sources"]:
        (output / directory).mkdir(exist_ok=True)
    (output / "vault_project.json").write_text(project.model_dump_json(indent=2))
    sources = {}
    for name, stage in project.stages.items():
        target = output / "sources" / f"{name}.csv"
        shutil.copyfile(project_path.parent / stage.path, target)
        sources[name] = {
            "path": str(target.relative_to(output)),
            "columns": stage.columns,
        }
        query = stage_sql(project, name, "unused.csv", "2000-01-01T00:00:00Z").split(
            " AS\n", 1
        )[1]
        # Replace only the raw-source CTE; derivation/null/hash SQL stays unchanged.
        query = (
            "WITH raw AS (SELECT * FROM raw."
            + q(name)
            + "),\nderived AS"
            + query.split("),\nderived AS", 1)[1]
        )
        query = query.replace(
            "'2000-01-01T00:00:00Z'", "CAST(@execution_time AS VARCHAR)"
        )
        (output / "models" / f"stage_{name}.sql").write_text(
            f"MODEL (name staging.{q(name)}, kind FULL, dialect duckdb);\n\n{query}\n"
        )
    for name, source in sources.items():
        project.stages[name].path = source["path"]
    (output / "vault_project.json").write_text(project.model_dump_json(indent=2))
    (output / "sources.json").write_text(json.dumps(sources, indent=2))
    by_name = {m.name: m for m in project.models}
    for model in project.models:
        plan = model_plan(project, model)
        raw_create, candidate_create = sqlglot.parse(plan.prepare, read="duckdb")
        raw_query = raw_create.expression.sql(dialect="duckdb")
        candidate_query = candidate_create.expression.sql(dialect="duckdb")
        select_query = sqlglot.parse_one(plan.insert, read="duckdb").expression.sql(
            dialect="duckdb"
        )
        for stage_name in project.stages:
            raw_query = raw_query.replace(
                q("_hv_stage_" + stage_name), "staging." + q(stage_name)
            )
        candidate_query = candidate_query.replace(q("_hv_raw_" + model.name), "__raw")
        select_query = select_query.replace(
            q("_hv_input_" + model.name), "source_data"
        ).replace(q(model.name), "@this_model")
        prefix = f"WITH __raw AS ({raw_query}), source_data AS ({candidate_query})"
        if select_query.startswith("WITH "):
            select_query = prefix + ", " + select_query[5:]
        else:
            select_query = prefix + " " + select_query
        columns = ", ".join(f"{q(c)} {t}" for c, t in types_for(project, model).items())
        key = [model.src_pk, model.src_ldts] if model.kind == "sat" else [model.src_pk]
        grain = ", ".join(q(c) for c in key)
        audits = [
            f"not_null(columns := ({q(model.src_pk)}, {q(model.src_ldts)}))",
            f"unique_combination_of_columns(columns := ({grain}))",
        ]
        references = (
            model.references
            if model.kind == "link"
            else ({model.src_pk: model.parent} if model.parent else {})
        )
        for fk, parent in references.items():
            audit_name = model.name + "_" + fk.lower() + "_relationship"
            (output / "audits" / f"{audit_name}.sql").write_text(
                f"AUDIT (name {audit_name}, blocking true);\nSELECT s.* FROM @this_model s LEFT JOIN vault.{q(parent)} p ON s.{q(fk)} = p.{q(by_name[parent].src_pk)} WHERE p.{q(by_name[parent].src_pk)} IS NULL;\n"
            )
            audits.append(audit_name)
        # Explicit dependencies ensure parent audit relations are scheduled first.
        deps = [f"staging.{q(s)}" for s in model.stages()] + [
            f"vault.{q(d)}" for d in model.dependencies()
        ]
        header = f"MODEL (name vault.{q(model.name)}, kind INCREMENTAL_UNMANAGED, dialect duckdb, start '2026-01-01', columns ({columns}), grain ({grain}), depends_on ({', '.join(deps)}), audits ({', '.join(audits)}));"
        (output / "models" / f"vault_{model.name}.sql").write_text(
            header + "\n\n" + select_query + ";\n"
        )
    external = [
        {"name": "raw." + name, "columns": {c: "VARCHAR" for c in stage.columns}}
        for name, stage in project.stages.items()
    ]
    (output / "external_models.yaml").write_text(json.dumps(external, indent=2))
    (output / "config.py").write_text("""from pathlib import Path
from sqlmesh.core.config import Config, GatewayConfig, DuckDBConnectionConfig, ModelDefaultsConfig
config = Config(
    gateways={"local": GatewayConfig(connection=DuckDBConnectionConfig(database=str(Path(__file__).parent / "warehouse.duckdb"), concurrent_tasks=1))},
    default_gateway="local", model_defaults=ModelDefaultsConfig(dialect="duckdb", start="2026-01-01"),
)
""")
    manifest = {
        "models": [m.name for m in project.models],
        "source_project": project.name,
        "engine": "sqlmesh",
        "kind": "INCREMENTAL_UNMANAGED",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
