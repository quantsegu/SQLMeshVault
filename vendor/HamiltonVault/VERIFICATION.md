# Verification — v0.2.0

Local environment: Python 3.13, Apache Hamilton 1.90.0, DuckDB 1.5.5.
`requirements-tested.txt` records the full dependency snapshot.

- **62 tests passed** (54 v2 cases including parametrized hash vectors, 8 legacy cases).
- Fresh end-to-end CLI run succeeded: 3 customer hubs, 2 order hubs, 2 links,
  4 customer satellite rows, 3 order satellite rows.
- All 16 persisted-data quality checks passed.
- Generated standalone SQL produced exactly the same data as Hamilton execution
  and replayed without additional vault rows in the integration test.
- Build, schema, metadata import and validation CLI commands succeeded.
- Hash fixtures cover MD5/SHA-256, binary/hex, scalar/list/null behavior, ordering
  and hashdiff sorting; custom separator/null/casing settings are also tested.
- Integration cases cover multi-source loading, history/reversion, unchanged loads,
  rollback, late changes/filtering, null-key counts, orphan rejection, timestamp
  validation, key collisions, schema drift and existing-table adoption refusal.

The upstream AutomateDV/dbt runtime, native Hackolade exports, cloud warehouses,
and production-scale loads have not been executed. The GitHub Actions workflow
is supplied but has not been run remotely. See COMPATIBILITY.md for the boundary.

`compiled-example/run_results.json` is the actual fresh-run report.
