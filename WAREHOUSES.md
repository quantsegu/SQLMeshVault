# Warehouse capability

The new warehouse paths support configuration and SQL generation for Databricks, Snowflake, and ClickHouse. DuckDB remains the default for existing local examples. No live warehouse tests were performed. Offline SQL compilation and driver stubs do not certify production execution.

| Backend | Metric compilation | Vault / execution |
|---|---|---|
| Databricks | Native MetricFlow renderer | Dialect SQL; SQL Connector or native SQLMesh gateway |
| Snowflake | Native MetricFlow renderer | Dialect SQL; Python Connector or native SQLMesh gateway |
| ClickHouse | DuckDB MetricFlow renderer translated through SQLGlot; limited bridge | ClickHouse SQL and MergeTree; clickhouse-connect or native SQLMesh gateway |

ClickHouse is not a native upstream MetricFlow backend. Unsupported translations fail rather than silently falling back. Use a modern ClickHouse release (25.8+ recommended) for correlated subqueries and window queries; this version floor has not been live certified. Do not assume complete MetricFlow feature parity. Remote MetricFlow bound parameters currently fail closed; use requests that compile to resolved SQL.

## Connection configuration

Copy the matching file from `examples/warehouses/`. Connection-only files contain `type` and `connection`. Vault and calculated metric specifications embed that object under `warehouse`.

```json
{"type":"snowflake","connection":{"account":{"env":"SNOWFLAKE_ACCOUNT"},"user":{"env":"SNOWFLAKE_USER"},"password":{"env":"SNOWFLAKE_PASSWORD"},"warehouse":"COMPUTE_WH","database":"DEMO"}}
```

Environment variables are resolved only when executing, never while compiling. Secret fields require environment references; do not put secret values in JSON or commit credentials. The examples are placeholders, not configured accounts. Install the appropriate optional driver extra in the existing project environment (`pip install '.[databricks]'`, `'.[snowflake]'`, or `'.[clickhouse]'`), retaining the repository's documented vendored dependency setup. Building offline does not require these connectors.

Databricks and Snowflake accept `catalog.schema.table` / `database.schema.table`; ClickHouse uses `database.table` and rejects a catalog. Configured identifier spelling and case are preserved: match existing Snowflake quoted identifiers exactly. UTC sessions and ClickHouse nullable joins are configured explicitly.

## Execution boundaries

Compilation writes local artifacts only. Query, run, or apply commands connect and can write to the configured destination; these commands were not executed against remote warehouses during development. Precreate landing/source relations and grant the execution identity access to source and destination schemas. Remote vault paths do not upload local CSV files.

Use a dedicated staging schema and a single writer with immutable source data during each run. Remote runs do not offer whole-batch rollback, the DuckDB run ledger, automatic schema migration, or concurrency protection. A failed run can leave partial writes. SQLMesh audits may run after materialization. Review generated SQL and plan before production use.

Generated SQLMesh projects use local DuckDB state in `sqlmesh_state.duckdb`, suitable for a single host. Configure an appropriate shared SQLMesh state backend for distributed production deployment. Warehouse data resides in the target engine; local state is orchestration metadata.

## References

- [Databricks SQL Connector](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector)
- [Snowflake Python Connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example)
- [ClickHouse Python client](https://clickhouse.com/docs/integrations/language-clients/python/index)
- SQLMesh engine configuration: [Databricks](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/databricks/), [Snowflake](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/snowflake/), [ClickHouse](https://sqlmesh.readthedocs.io/en/stable/integrations/engines/clickhouse/).

## SQLMeshVault usage

```sh
sqlmesh-vault build-warehouse vendor/HamiltonVault/examples/replacement/project.json --warehouse examples/warehouses/databricks-vault.json --output build/databricks
# Executes and writes remotely; not run in the offline tests:
sqlmesh-vault apply build/databricks
```

The compiler emits native SQLMesh stage, hub, link, satellite and audit models plus a gateway. `source_tables` maps source metadata names to warehouse landing relations. `target_schema` and `staging_schema` control placement. Update `load_dts` for each batch. Existing DuckDB build commands remain available.

## Validation record

The local regression suite passed **21 tests** on 2026-09-18. The checked-in [JUnit report](reports/warehouse-regression.xml) includes DuckDB regressions, offline warehouse compilation and driver stubs. No Databricks, Snowflake or ClickHouse service was contacted. SQL parsing establishes compilation coverage, not live backend certification.
