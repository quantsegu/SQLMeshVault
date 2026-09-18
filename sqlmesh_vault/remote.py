"""Native SQLMesh project export for the warehouse vault plan."""

import json
from pathlib import Path

import yaml

from .warehouse import table
from .warehouse_vault import compile_project, data_type


def build(metadata, warehouse, output):
    plan = compile_project(metadata, warehouse, output)
    output = Path(output)
    engine = plan["warehouse"]["type"]
    (output / "models").mkdir()
    (output / "audits").mkdir()

    def model_file(name, query, kind, extra=""):
        relation = table(name).sql(dialect=engine)
        (output / "models" / (name + ".sql")).write_text(
            f"MODEL (name {relation}, kind {kind}, dialect {engine}{extra});\n{query};\n"
        )

    def audit_files(prefix, checks, self_relation):
        names = []
        for i, check in enumerate(checks):
            name = prefix + "_" + str(i)
            sql = check["sql"].replace(
                table(self_relation).sql(dialect=engine), "@this_model"
            )
            (output / "audits" / (name + ".sql")).write_text(
                f"AUDIT (name {name}, dialect {engine}, blocking true);\n{sql};\n"
            )
            names.append(name)
        return ", audits (" + ", ".join(names) + ")" if names else ""

    for stage, spec in plan["stages"].items():
        model_file(
            spec["relation"],
            spec["query"],
            "FULL",
            audit_files("stage_" + stage, spec["checks"], spec["relation"]),
        )
    for name, spec in plan["models"].items():
        for prepared in spec["prepare"]:
            model_file(prepared["relation"], prepared["query"], "VIEW")
        query = spec["query"].replace(
            table(spec["relation"]).sql(dialect=engine), "@this_model"
        )
        columns = ", ".join(
            table(c).sql(dialect=engine)
            + " "
            + data_type(t, engine, c not in spec["required"])
            for c, t in spec["columns"].items()
        )
        checks = spec["checks"] + [
            c for c in plan["quality"] if c["name"].startswith(name + ":")
        ]
        extra = (
            ", columns ("
            + columns
            + ")"
            + audit_files("vault_" + name, checks, spec["relation"])
        )
        dependencies = [
            table(plan["models"][d]["relation"]).sql(dialect=engine)
            for d in spec["dependencies"]
        ]
        if dependencies:
            extra += ", depends_on (" + ", ".join(dependencies) + ")"
        model_file(spec["relation"], query, "INCREMENTAL_UNMANAGED", extra)
    external = [
        {
            "name": plan["config"]["source_tables"][name],
            "columns": {c: "VARCHAR" for c in s["columns"]},
        }
        for name, s in plan["metadata"]["stages"].items()
    ]
    (output / "external_models.yaml").write_text(yaml.safe_dump(external))
    (output / "config.py").write_text(
        "from pathlib import Path\nimport json\nfrom sqlmesh_vault.warehouse import sqlmesh_config\nroot=Path(__file__).parent\nconfig=sqlmesh_config(json.loads((root/'warehouse-plan.json').read_text())['warehouse'],root)\n"
    )
    return {
        "engine": engine,
        "models": [s["relation"] for s in plan["models"].values()],
        "validation": plan["validation"],
    }


def apply(path, execution_time):
    from sqlmesh import Context

    path = Path(path)
    plan = json.loads((path / "warehouse-plan.json").read_text())
    context = Context(paths=path)
    try:
        tested = context.test()
        if not tested.wasSuccessful():
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
            raise RuntimeError("Warehouse vault run failed")
        return {
            "status": "success",
            "engine": plan["warehouse"]["type"],
            "model_tests": tested.testsRun,
            "atomic_batch": False,
        }
    finally:
        context.close()
