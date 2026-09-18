# Compatibility and migration

## Supported replacement boundary

This project is an independent implementation. It does not include or invoke
dbt or AutomateDV. It replaces core loading patterns on the tested DuckDB backend;
it does not claim complete package parity or support for existing dbt projects
without metadata conversion.

| Capability | Status |
|---|---|
| Stage derived aliases/constants/run timestamp | Implemented |
| Explicit null replacements | Implemented; own mapping contract |
| MD5/SHA-256, binary/hex hashes | Implemented; documented text vectors tested |
| Hash casing/separator/null marker options | Implemented |
| Multi-source hub/link inserts | Implemented |
| Ordinary hub/link satellites | Implemented |
| Multiple changes per batch and reversion history | Implemented |
| Effective timestamps / extra columns | Carried to target |
| Source filtering | Explicit `late_arriving: ignore` |
| Hamilton dependency execution and atomic batches | Implemented |
| DDL/DML export, manifest, run results, quality tests | Implemented |
| Legacy v1 starter projects | Preserved through version routing |
| Resolved macro metadata import | Implemented for core subset |
| Native dbt Jinja project/manifest parsing | Not implemented |
| Arbitrary derived SQL, ranked/excluded columns | Not implemented |
| Multi-active/effectivity/extended-tracking satellites | Not implemented |
| Transactional links, reference-table macros, PIT/bridge | Not implemented |
| Ghost records and automatic schema evolution | Not implemented |
| Snowflake, Databricks, BigQuery, SQL Server adapters | Not implemented |
| Actual native Hackolade project import | Requires export-specific normalization |

## Hash compatibility

The implementation follows the published AutomateDV text normalization and
concatenation conventions for the supported settings. SQL output is checked
against independently calculated hash vectors, including scalar/list null cases,
alphabetic hashdiff sorting, explicit key ordering, and binary/hex representations.
It is **not** an upstream dbt/AutomateDV conformance test run.

Before combining with an existing vault, establish parity using representative
source rows and actual target hashes. Match algorithm, normalization, alias/order,
separator, null marker and storage representation exactly. Binary hashes must be
compared as bytes (or normalized hex), not display strings. Hex output here is
lowercase; display casing is not a change in underlying bytes.

CSV inputs remain strings, so `1`, `1.0`, dates and formatted decimals are not
interchangeable. DuckDB string casing/casting rules can differ from another
warehouse, especially for Unicode or typed inputs. Only spaces are trimmed by
SQL TRIM; do not assume every whitespace character is treated alike everywhere.
Delimiter/null-marker ambiguity is inherited from this concatenation convention;
for example a literal placeholder and a missing value can produce the same input.
Hub/link collision checks reject some ambiguous mappings, but this does not make
concatenation collision-free. Do not change framing on an existing hashed vault.

Version 1 uses a different SHA-256 JSON framing and cannot share keys with version
2. Existing v1 databases are not upgraded in place.

## Loading differences

- This runner requires explicit parent metadata and checks parent existence.
- Conflicting source states for a satellite at the same timestamp fail instead of
  relying on input ordering. Any same-time payload difference is a conflict,
  including differences that normalize to the same hash.
- By default an unknown historical change fails; `late_arriving: ignore` filters
  records at or before the persisted per-key latest timestamp. This is deliberate
  and differs from assuming all input is already a clean delta feed.
- Hashdiff controls new history. Case/space changes erased by normalization do not
  generate new rows. Include all fields whose changes matter in the hashdiff;
  `src_eff` and extra columns are not automatically added to it.
- Hub/link natural-key payloads are retained from the earliest eligible source
  row for a new hash. Same-time source priority follows `source_model` list order.
- Multi-source models require the same output column aliases. Complex mapping,
  business-key collision codes or salting require explicit stage preparation.
- All non-hash, non-timestamp payloads are VARCHAR in this backend.

## Migration procedure

1. Inventory every dbtVault macro, stage rule and warehouse-specific override.
   Match them against the table above. Unsupported models must stay on the
   existing implementation until their replacement is implemented and tested.
2. Export resolved metadata using `examples/replacement/automatedv.export.json`
   as the interchange template. Include physical CSV source locations, exact
   headers, parent relationships, and hash variables. Replace arbitrary derived
   SQL with supported rules or prepare those fields upstream.
3. Run `import-automatedv`, `validate`, and `build`. Review the manifest and
   generated SQL. Do not assume that a passing schema validates business-key design.
4. Load into a **new shadow DuckDB database** from immutable source snapshots.
   Compare keys as normalized hex, natural keys, FK relationships, record sources,
   load timestamps and every satellite transition against existing vault exports.
   Exercise unchanged loads, duplicate delivery, multiple changes and reversions.
5. Resolve every mismatch in metadata or implementation. Run `test` and the
   integration suite. Plan source watermarks and writer ownership before cutover.
6. Cut over consumers only after your data comparisons and unsupported-feature
   work are complete. Preserve the original implementation/database for rollback.

This repository does not automate an existing database's adoption or a warehouse
cutover. No production system has been accessed or changed.

## Reference contracts

- [AutomateDV macro API](https://automate-dv.readthedocs.io/en/latest/macros/)
- [AutomateDV hashing](https://automate-dv.readthedocs.io/en/latest/best_practises/hashing/)
- [AutomateDV load behavior](https://automate-dv.readthedocs.io/en/latest/best_practises/loading/)
- [Apache Hamilton driver](https://hamilton.apache.org/concepts/driver/)
- [Hackolade exports](https://hackolade.com/help/Exportorforward-engineer.html)
