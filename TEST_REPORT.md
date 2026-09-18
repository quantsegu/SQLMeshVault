# Test report

| Suite | Passed | Failed |
|---|---:|---:|
| SQLMeshVault integration | 6 | 0 |
| Original HamiltonVault tests | 62 | 0 |
| Selected SQLMesh core, DuckDB and date suites | 1113 | 1 |
| Failing custom-kind test, isolated rerun | 1 | 0 |
| Complete model test file, isolated rerun | 341 | 0 |

The integration performs real native SQLMesh plans, applies, runs, model tests and
audits. It compares every persisted vault row with the original HamiltonVault
runner, tests replay, appended satellite history, immutable source enforcement,
invalid-batch rejection and changed-source scheduling protection. Generated model
tests also verify staging output through SQLMesh's native test runner.

The broad upstream process failed `test_custom_kind`: a materialization class had
already been registered by an earlier test, so an expected missing-class exception
was not raised. The unchanged test passed in a fresh process, and all 341 tests in its model test
file passed when that file ran separately. The reproduction
script isolates test files to avoid registration leakage; the original failed
combined-run report is retained. Isolated reruns are not counted as new coverage.

The broad selection comprises audit, dialect, snapshot, seed, test framework, plan,
context, model, schema-diff, DuckDB adapter and date tests. Full copied sources:
1,726 SQLMesh files and 46 HamiltonVault files, each hash-verified.

PySpark 3.5 test extras require NumPy <2 when Hamilton discovers optional plugins;
the Hatch environment explicitly pins a compatible NumPy version.

Reference suite: `hatch run python -m pytest vendor/HamiltonVault/tests -p no:rerunfailures -q`.

## Reproduction and scope

Run `hatch run test` for the added integration and
`hatch run python scripts/run_upstream_tests.py` for the documented upstream selection.
JUnit XML files are in `reports/`; `environment.txt` records installed versions.
Installed wheel CLI entry points were smoke-tested from outside the source tree.
`python scripts/verify_sources.py` validates every copied upstream file, including symlinks.

Full source copies do not mean all upstream tests were executed. External warehouse,
cloud service, dbt end-to-end, slow/performance and complete cross-platform matrices
were not validated. Tests ran on macOS ARM64 with Python 3.11 and local DuckDB.
Deprecation warnings remain in upstream dependencies. No upstream code was changed
to suppress failures. GitHub CI runs the new integration tests and source verification.
