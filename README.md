# SQLMeshVault

Complete SQLMesh and HamiltonVault source snapshots, plus a library that generates
and executes **native SQLMesh Data Vault models**.

This is executable integration code: SQLMesh plans, snapshots, backfills, materializes
and audits the generated models. The vault tables use `INCREMENTAL_UNMANAGED`
with insert-only anti-joins and satellite change windows. Raw event histories are
validated before SQLMesh writes; keys and payloads match the HamiltonVault reference.

## Complete source copies

- `vendor/sqlmesh/`: every tracked upstream file at
  `ad2377ea9f065b26d957207958a024aaebc0b70f`.
- `vendor/HamiltonVault/`: every tracked file at the revision recorded in
  `UPSTREAM.lock.json`, including both vault implementations and their test suites.
- `sqlmesh_vault/`: the new native SQLMesh compiler, preflight loader and CLI.

These are full source snapshots, not copies of Git history. Original source,
licenses and notices are preserved. `python scripts/verify_sources.py` checks every
copied file. This independent project is not an official SQLMesh release.

## Run the working example

Use Python 3.11 and Hatch. The Hatch environment installs the bundled sources.

```sh
hatch run python -m sqlmesh_vault.cli build vendor/HamiltonVault/examples/replacement/project.json --output build/demo
cp examples/model_tests.yaml build/demo/tests/test_stage.yaml
hatch run python -m sqlmesh_vault.cli apply build/demo --execution-time 2026-01-05T00:00:00Z
hatch run test
```

Expected counts: customer hubs 3, order hubs 2, links 2, customer satellite rows 4,
order satellite rows 3. Two native SQLMesh model tests run before plan/apply.
The integration suite compares **every stored row** with HamiltonVault, exercises
replay and appended history, and verifies rejected batches do not mutate raw/vault data.

## Generated project

- `config.py`: local DuckDB SQLMesh gateway and model defaults.
- `models/stage_*.sql`: FULL staging models with the same derived/null/hash logic.
- `models/vault_hub_*.sql`, `vault_link_*.sql`, `vault_sat_*.sql`: native append-only models.
- `audits/`: blocking referential checks; models also declare uniqueness/null audits.
- `external_models.yaml`: raw source table contracts.
- `sources/`: immutable-history CSV feeds ingested into the `raw` schema.
- `vault_project.json`: validated metadata used by the preflight.
- `manifest.json`: generated model inventory.
- `warehouse.duckdb`: runtime data and SQLMesh state (not committed).

Use the same version-2 metadata contract as HamiltonVault. Hashing and core
hub/link/ordinary-satellite behavior are preserved. The full source copy does not
add missing HamiltonVault macro types: see its `COMPATIBILITY.md` for that scope.

## Updating source data

Append events to `build/demo/sources/crm.csv`, retaining earlier events, then run
with a later **daily** execution interval:

```sh
hatch run python -m sqlmesh_vault.cli apply build/demo --execution-time 2026-01-07T00:00:00Z
```

Existing input events may not be removed or modified. A changed source in an
already processed daily interval is rejected instead of returning stale results.
The load-state sidecar records successful execution time and source fingerprint;
keep it with the generated project. Repeated identical input is idempotent.
Keep sources immutable during a run and use one writer per project/database.

Before syncing raw data, the library runs the original vault validations against
an isolated copy of current vault tables. Conflicting same-time states, orphan
keys and unknown late changes fail before raw tables change. SQLMesh then runs
its own native model tests and blocking data audits.

SQLMesh transactions are **per model**, not one atomic transaction across the
whole DAG. Infrastructure failures midway through a run can leave earlier models
updated; rerun the idempotent pipeline after fixing the failure. The preflight is
not a distributed transaction. CSV ingestion/preflight copies use memory and have
not been benchmarked at warehouse scale.

The original HamiltonVault runner remains available in the bundled source for
its different whole-batch transaction behavior. Avoid destructive SQLMesh
restatements unless the complete source history is available for reconstruction.

## Native APIs and tests

```python
from sqlmesh_vault.compiler import build
from sqlmesh_vault.runtime import apply
build('project.json', 'build/vault')
report = apply('build/vault', '2026-01-07T00:00:00Z')
```

Generated projects are ordinary SQLMesh projects and can also be inspected with
its native `Context` and CLI. Use this library's `apply` entry point when you need
its preflight and immutable-source checks; direct SQLMesh commands bypass them.
The provided gateway and generated SQL are tested on DuckDB. The full upstream
warehouse adapters remain in the source, but this generator is not a tested
Snowflake/Databricks deployment adapter.

`TEST_REPORT.md` records actual integration, reference and upstream test runs,
including limitations. `reports/` contains JUnit evidence.

## Warehouse adapters

See [WAREHOUSES.md](WAREHOUSES.md) for Databricks, Snowflake, and ClickHouse configuration, target placement, offline validation, and execution limitations.
