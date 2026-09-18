import shutil
from pathlib import Path

import duckdb
import pytest
from sqlmesh_vault.compiler import build
from sqlmesh_vault.runtime import apply
from vault.v2.engine import run as reference_run

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "vendor/HamiltonVault/examples/replacement/project.json"


@pytest.fixture
def project(tmp_path):
    out = tmp_path / "native"
    build(SOURCE, out)
    shutil.copy(ROOT / "examples/model_tests.yaml", out / "tests/test_stage.yaml")
    return out


def fetch(project, table):
    with duckdb.connect(str(project / "warehouse.duckdb")) as db:
        db.execute("SET TimeZone='UTC'")
        return sorted(db.execute(f"SELECT * FROM vault.{table}").fetchall(), key=str)


def test_native_plan_apply_parity_and_replay(project, tmp_path):
    report = apply(project)
    assert report["model_tests"] == 2
    assert report["counts"] == {
        "hub_customer": 3,
        "hub_order": 2,
        "link_customer_order": 2,
        "sat_customer": 4,
        "sat_order": 3,
    }
    reference = tmp_path / "reference.duckdb"
    reference_run(SOURCE, reference, "compare", "2026-01-05T00:00:00Z")
    with duckdb.connect(str(reference)) as db:
        db.execute("SET TimeZone='UTC'")
        for table in report["counts"]:
            assert fetch(project, table) == sorted(
                db.execute(f"SELECT * FROM {table}").fetchall(), key=str
            )
    assert apply(project)["counts"] == report["counts"]


def test_append_history_and_keep_original_hub(project):
    apply(project)
    original = fetch(project, "hub_customer")
    path = project / "sources/crm.csv"
    path.write_text(path.read_text() + "C001,Alice,Geneva,2026-01-06T00:00:00Z\n")
    report = apply(project, "2026-01-07T00:00:00Z")
    assert report["counts"]["sat_customer"] == 5
    assert fetch(project, "hub_customer") == original
    assert apply(project, "2026-01-07T00:00:00Z")["counts"] == report["counts"]


def test_invalid_batch_does_not_mutate_vault_or_raw(project):
    apply(project)
    before = fetch(project, "sat_customer")
    path = project / "sources/crm.csv"
    path.write_text(
        path.read_text()
        + "C004,New,Basel,2026-01-06T00:00:00Z\nC001,Alice,Geneva,2026-01-04T00:00:00Z\n"
    )
    with pytest.raises(ValueError, match="conflicting states"):
        apply(project, "2026-01-07T00:00:00Z")
    assert fetch(project, "sat_customer") == before
    with duckdb.connect(str(project / "warehouse.duckdb")) as db:
        assert db.execute("SELECT COUNT(*) FROM raw.crm").fetchone()[0] == 5


def test_source_history_cannot_be_rewritten(project):
    apply(project)
    path = project / "sources/crm.csv"
    path.write_text(path.read_text().replace("Zurich", "Lausanne"))
    with pytest.raises(ValueError, match="source history"):
        apply(project, "2026-01-07T00:00:00Z")


def test_native_model_kinds(project):
    sql = (project / "models/vault_sat_customer.sql").read_text()
    assert "INCREMENTAL_UNMANAGED" in sql and "@this_model" in sql
    assert "LAG(" in sql
    assert list((project / "audits").glob("*.sql"))


def test_changed_input_requires_new_execution_interval(project):
    apply(project)
    path = project / "sources/crm.csv"
    path.write_text(path.read_text() + "C004,New,Basel,2026-01-04T00:00:00Z\n")
    with pytest.raises(ValueError, match="later daily execution interval"):
        apply(project)
    assert len(fetch(project, "hub_customer")) == 3
