-- Reviewable SQL export; Hamilton run also enforces ownership, schema, source and audit checks.

SET TimeZone = 'UTC';

BEGIN TRANSACTION;

CREATE OR REPLACE TEMP TABLE "_hv_stage_crm" AS
WITH raw AS (SELECT * FROM read_csv('examples/replacement/crm.csv', header=true, auto_detect=false, delim=',', quote='"', escape='"', columns={'CUSTOMER_ID': 'VARCHAR', 'NAME': 'VARCHAR', 'CITY': 'VARCHAR', 'INGESTED_AT': 'VARCHAR'}, nullstr='', strict_mode=true)),
derived AS (SELECT *, "INGESTED_AT" AS "LOAD_DTS", 'CRM' AS "RECORD_SOURCE" FROM raw),
null_replaced AS (SELECT "CUSTOMER_ID" AS "CUSTOMER_ID", "NAME" AS "NAME", "CITY" AS "CITY", "INGESTED_AT" AS "INGESTED_AT", "LOAD_DTS" AS "LOAD_DTS", "RECORD_SOURCE" AS "RECORD_SOURCE" FROM derived)
SELECT *, FROM_HEX(MD5(NULLIF(UPPER(TRIM(CAST("CUSTOMER_ID" AS VARCHAR))), ''))) AS "CUSTOMER_HK", FROM_HEX(MD5(CONCAT_WS('||', COALESCE(NULLIF(UPPER(TRIM(CAST("CITY" AS VARCHAR))), ''), '^^'), COALESCE(NULLIF(UPPER(TRIM(CAST("NAME" AS VARCHAR))), ''), '^^')))) AS "CUSTOMER_HASHDIFF" FROM null_replaced;
-- crm.LOAD_DTS: use ISO timestamps with explicit timezone
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_stage_crm"
WHERE "LOAD_DTS" IS NOT NULL AND
(NOT regexp_matches(CAST("LOAD_DTS" AS VARCHAR), '(Z|[+-][0-9]{2}:[0-9]{2})$', 'i')
 OR TRY_CAST("LOAD_DTS" AS TIMESTAMPTZ) IS NULL)) THEN error('crm.LOAD_DTS: use ISO timestamps with explicit timezone') ELSE 'ok' END;

CREATE OR REPLACE TEMP TABLE "_hv_stage_erp" AS
WITH raw AS (SELECT * FROM read_csv('examples/replacement/erp.csv', header=true, auto_detect=false, delim=',', quote='"', escape='"', columns={'ACCOUNT_ID': 'VARCHAR', 'ORDER_ID': 'VARCHAR', 'AMOUNT': 'VARCHAR', 'STATUS': 'VARCHAR', 'INGESTED_AT': 'VARCHAR', 'EFFECTIVE_AT': 'VARCHAR'}, nullstr='', strict_mode=true)),
derived AS (SELECT *, "ACCOUNT_ID" AS "CUSTOMER_ID", "INGESTED_AT" AS "LOAD_DTS", 'ERP' AS "RECORD_SOURCE" FROM raw),
null_replaced AS (SELECT "ACCOUNT_ID" AS "ACCOUNT_ID", "ORDER_ID" AS "ORDER_ID", "AMOUNT" AS "AMOUNT", "STATUS" AS "STATUS", "INGESTED_AT" AS "INGESTED_AT", "EFFECTIVE_AT" AS "EFFECTIVE_AT", "CUSTOMER_ID" AS "CUSTOMER_ID", "LOAD_DTS" AS "LOAD_DTS", "RECORD_SOURCE" AS "RECORD_SOURCE" FROM derived)
SELECT *, FROM_HEX(MD5(NULLIF(UPPER(TRIM(CAST("CUSTOMER_ID" AS VARCHAR))), ''))) AS "CUSTOMER_HK", FROM_HEX(MD5(NULLIF(UPPER(TRIM(CAST("ORDER_ID" AS VARCHAR))), ''))) AS "ORDER_HK", FROM_HEX(MD5(NULLIF(CONCAT_WS('||', COALESCE(NULLIF(UPPER(TRIM(CAST("CUSTOMER_ID" AS VARCHAR))), ''), '^^'), COALESCE(NULLIF(UPPER(TRIM(CAST("ORDER_ID" AS VARCHAR))), ''), '^^')), '^^||^^'))) AS "CUSTOMER_ORDER_HK", FROM_HEX(MD5(CONCAT_WS('||', COALESCE(NULLIF(UPPER(TRIM(CAST("AMOUNT" AS VARCHAR))), ''), '^^'), COALESCE(NULLIF(UPPER(TRIM(CAST("STATUS" AS VARCHAR))), ''), '^^')))) AS "ORDER_HASHDIFF" FROM null_replaced;
-- erp.EFFECTIVE_AT: use ISO timestamps with explicit timezone
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_stage_erp"
WHERE "EFFECTIVE_AT" IS NOT NULL AND
(NOT regexp_matches(CAST("EFFECTIVE_AT" AS VARCHAR), '(Z|[+-][0-9]{2}:[0-9]{2})$', 'i')
 OR TRY_CAST("EFFECTIVE_AT" AS TIMESTAMPTZ) IS NULL)) THEN error('erp.EFFECTIVE_AT: use ISO timestamps with explicit timezone') ELSE 'ok' END;
-- erp.LOAD_DTS: use ISO timestamps with explicit timezone
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_stage_erp"
WHERE "LOAD_DTS" IS NOT NULL AND
(NOT regexp_matches(CAST("LOAD_DTS" AS VARCHAR), '(Z|[+-][0-9]{2}:[0-9]{2})$', 'i')
 OR TRY_CAST("LOAD_DTS" AS TIMESTAMPTZ) IS NULL)) THEN error('erp.LOAD_DTS: use ISO timestamps with explicit timezone') ELSE 'ok' END;

CREATE TABLE IF NOT EXISTS "hub_customer" (
  "CUSTOMER_HK" BLOB NOT NULL,
  "CUSTOMER_ID" VARCHAR,
  "LOAD_DTS" TIMESTAMPTZ NOT NULL,
  "RECORD_SOURCE" VARCHAR NOT NULL,
  PRIMARY KEY ("CUSTOMER_HK")
);

CREATE OR REPLACE TEMP TABLE "_hv_raw_hub_customer" AS SELECT "CUSTOMER_HK", "CUSTOMER_ID", "LOAD_DTS", "RECORD_SOURCE", 0 AS "__priority" FROM "_hv_stage_crm"
UNION ALL
SELECT "CUSTOMER_HK", "CUSTOMER_ID", "LOAD_DTS", "RECORD_SOURCE", 1 AS "__priority" FROM "_hv_stage_erp";
CREATE OR REPLACE TEMP TABLE "_hv_input_hub_customer" AS SELECT CAST("CUSTOMER_HK" AS BLOB) AS "CUSTOMER_HK", CAST("CUSTOMER_ID" AS VARCHAR) AS "CUSTOMER_ID", CAST("LOAD_DTS" AS TIMESTAMPTZ) AS "LOAD_DTS", CAST("RECORD_SOURCE" AS VARCHAR) AS "RECORD_SOURCE", "__priority" FROM "_hv_raw_hub_customer" WHERE "CUSTOMER_HK" IS NOT NULL;

-- hub_customer: null load timestamp or record source
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_hub_customer" WHERE "LOAD_DTS" IS NULL OR NULLIF(TRIM("RECORD_SOURCE"), '') IS NULL) THEN error('hub_customer: null load timestamp or record source') ELSE 'ok' END;
-- hub_customer: key collision within batch
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_HK" FROM (SELECT DISTINCT "CUSTOMER_HK", NULLIF(UPPER(TRIM(CAST("CUSTOMER_ID" AS VARCHAR))), '') FROM "_hv_input_hub_customer") x GROUP BY "CUSTOMER_HK" HAVING COUNT(*) > 1) THEN error('hub_customer: key collision within batch') ELSE 'ok' END;
-- hub_customer: key collision with target
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_hub_customer" s JOIN "hub_customer" t ON s."CUSTOMER_HK" = t."CUSTOMER_HK" WHERE NULLIF(UPPER(TRIM(CAST(s."CUSTOMER_ID" AS VARCHAR))), '') IS DISTINCT FROM NULLIF(UPPER(TRIM(CAST(t."CUSTOMER_ID" AS VARCHAR))), '')) THEN error('hub_customer: key collision with target') ELSE 'ok' END;

INSERT INTO "hub_customer" ("CUSTOMER_HK", "CUSTOMER_ID", "LOAD_DTS", "RECORD_SOURCE")
SELECT "CUSTOMER_HK", "CUSTOMER_ID", "LOAD_DTS", "RECORD_SOURCE" FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_HK" ORDER BY "LOAD_DTS", "__priority", "CUSTOMER_ID", "RECORD_SOURCE") AS "__rank"
  FROM "_hv_input_hub_customer"
) s WHERE "__rank" = 1
AND NOT EXISTS (SELECT 1 FROM "hub_customer" t WHERE t."CUSTOMER_HK" = s."CUSTOMER_HK");

CREATE TABLE IF NOT EXISTS "hub_order" (
  "ORDER_HK" BLOB NOT NULL,
  "ORDER_ID" VARCHAR,
  "LOAD_DTS" TIMESTAMPTZ NOT NULL,
  "RECORD_SOURCE" VARCHAR NOT NULL,
  PRIMARY KEY ("ORDER_HK")
);

CREATE OR REPLACE TEMP TABLE "_hv_raw_hub_order" AS SELECT "ORDER_HK", "ORDER_ID", "LOAD_DTS", "RECORD_SOURCE", 0 AS "__priority" FROM "_hv_stage_erp";
CREATE OR REPLACE TEMP TABLE "_hv_input_hub_order" AS SELECT CAST("ORDER_HK" AS BLOB) AS "ORDER_HK", CAST("ORDER_ID" AS VARCHAR) AS "ORDER_ID", CAST("LOAD_DTS" AS TIMESTAMPTZ) AS "LOAD_DTS", CAST("RECORD_SOURCE" AS VARCHAR) AS "RECORD_SOURCE", "__priority" FROM "_hv_raw_hub_order" WHERE "ORDER_HK" IS NOT NULL;

-- hub_order: null load timestamp or record source
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_hub_order" WHERE "LOAD_DTS" IS NULL OR NULLIF(TRIM("RECORD_SOURCE"), '') IS NULL) THEN error('hub_order: null load timestamp or record source') ELSE 'ok' END;
-- hub_order: key collision within batch
SELECT CASE WHEN EXISTS (SELECT "ORDER_HK" FROM (SELECT DISTINCT "ORDER_HK", NULLIF(UPPER(TRIM(CAST("ORDER_ID" AS VARCHAR))), '') FROM "_hv_input_hub_order") x GROUP BY "ORDER_HK" HAVING COUNT(*) > 1) THEN error('hub_order: key collision within batch') ELSE 'ok' END;
-- hub_order: key collision with target
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_hub_order" s JOIN "hub_order" t ON s."ORDER_HK" = t."ORDER_HK" WHERE NULLIF(UPPER(TRIM(CAST(s."ORDER_ID" AS VARCHAR))), '') IS DISTINCT FROM NULLIF(UPPER(TRIM(CAST(t."ORDER_ID" AS VARCHAR))), '')) THEN error('hub_order: key collision with target') ELSE 'ok' END;

INSERT INTO "hub_order" ("ORDER_HK", "ORDER_ID", "LOAD_DTS", "RECORD_SOURCE")
SELECT "ORDER_HK", "ORDER_ID", "LOAD_DTS", "RECORD_SOURCE" FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY "ORDER_HK" ORDER BY "LOAD_DTS", "__priority", "ORDER_ID", "RECORD_SOURCE") AS "__rank"
  FROM "_hv_input_hub_order"
) s WHERE "__rank" = 1
AND NOT EXISTS (SELECT 1 FROM "hub_order" t WHERE t."ORDER_HK" = s."ORDER_HK");

CREATE TABLE IF NOT EXISTS "link_customer_order" (
  "CUSTOMER_ORDER_HK" BLOB NOT NULL,
  "CUSTOMER_HK" BLOB NOT NULL,
  "ORDER_HK" BLOB NOT NULL,
  "LOAD_DTS" TIMESTAMPTZ NOT NULL,
  "RECORD_SOURCE" VARCHAR NOT NULL,
  PRIMARY KEY ("CUSTOMER_ORDER_HK")
);

CREATE OR REPLACE TEMP TABLE "_hv_raw_link_customer_order" AS SELECT "CUSTOMER_ORDER_HK", "CUSTOMER_HK", "ORDER_HK", "LOAD_DTS", "RECORD_SOURCE", 0 AS "__priority" FROM "_hv_stage_erp";
CREATE OR REPLACE TEMP TABLE "_hv_input_link_customer_order" AS SELECT CAST("CUSTOMER_ORDER_HK" AS BLOB) AS "CUSTOMER_ORDER_HK", CAST("CUSTOMER_HK" AS BLOB) AS "CUSTOMER_HK", CAST("ORDER_HK" AS BLOB) AS "ORDER_HK", CAST("LOAD_DTS" AS TIMESTAMPTZ) AS "LOAD_DTS", CAST("RECORD_SOURCE" AS VARCHAR) AS "RECORD_SOURCE", "__priority" FROM "_hv_raw_link_customer_order" WHERE "CUSTOMER_ORDER_HK" IS NOT NULL AND "CUSTOMER_HK" IS NOT NULL AND "ORDER_HK" IS NOT NULL;

-- link_customer_order: null load timestamp or record source
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_link_customer_order" WHERE "LOAD_DTS" IS NULL OR NULLIF(TRIM("RECORD_SOURCE"), '') IS NULL) THEN error('link_customer_order: null load timestamp or record source') ELSE 'ok' END;
-- link_customer_order: orphan key CUSTOMER_HK -> hub_customer
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_link_customer_order" s WHERE NOT EXISTS (SELECT 1 FROM "hub_customer" p WHERE p."CUSTOMER_HK" = s."CUSTOMER_HK")) THEN error('link_customer_order: orphan key CUSTOMER_HK -> hub_customer') ELSE 'ok' END;
-- link_customer_order: orphan key ORDER_HK -> hub_order
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_link_customer_order" s WHERE NOT EXISTS (SELECT 1 FROM "hub_order" p WHERE p."ORDER_HK" = s."ORDER_HK")) THEN error('link_customer_order: orphan key ORDER_HK -> hub_order') ELSE 'ok' END;
-- link_customer_order: key collision within batch
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_ORDER_HK" FROM (SELECT DISTINCT "CUSTOMER_ORDER_HK", "CUSTOMER_HK", "ORDER_HK" FROM "_hv_input_link_customer_order") x GROUP BY "CUSTOMER_ORDER_HK" HAVING COUNT(*) > 1) THEN error('link_customer_order: key collision within batch') ELSE 'ok' END;
-- link_customer_order: key collision with target
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_link_customer_order" s JOIN "link_customer_order" t ON s."CUSTOMER_ORDER_HK" = t."CUSTOMER_ORDER_HK" WHERE s."CUSTOMER_HK" IS DISTINCT FROM t."CUSTOMER_HK" OR s."ORDER_HK" IS DISTINCT FROM t."ORDER_HK") THEN error('link_customer_order: key collision with target') ELSE 'ok' END;

INSERT INTO "link_customer_order" ("CUSTOMER_ORDER_HK", "CUSTOMER_HK", "ORDER_HK", "LOAD_DTS", "RECORD_SOURCE")
SELECT "CUSTOMER_ORDER_HK", "CUSTOMER_HK", "ORDER_HK", "LOAD_DTS", "RECORD_SOURCE" FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_ORDER_HK" ORDER BY "LOAD_DTS", "__priority", "CUSTOMER_HK", "ORDER_HK", "RECORD_SOURCE") AS "__rank"
  FROM "_hv_input_link_customer_order"
) s WHERE "__rank" = 1
AND NOT EXISTS (SELECT 1 FROM "link_customer_order" t WHERE t."CUSTOMER_ORDER_HK" = s."CUSTOMER_ORDER_HK");

CREATE TABLE IF NOT EXISTS "sat_customer" (
  "CUSTOMER_HK" BLOB NOT NULL,
  "CUSTOMER_HASHDIFF" BLOB NOT NULL,
  "NAME" VARCHAR,
  "CITY" VARCHAR,
  "LOAD_DTS" TIMESTAMPTZ NOT NULL,
  "RECORD_SOURCE" VARCHAR NOT NULL,
  PRIMARY KEY ("CUSTOMER_HK", "LOAD_DTS")
);

CREATE OR REPLACE TEMP TABLE "_hv_raw_sat_customer" AS SELECT "CUSTOMER_HK", "CUSTOMER_HASHDIFF", "NAME", "CITY", "LOAD_DTS", "RECORD_SOURCE", 0 AS "__priority" FROM "_hv_stage_crm";
CREATE OR REPLACE TEMP TABLE "_hv_input_sat_customer" AS SELECT CAST("CUSTOMER_HK" AS BLOB) AS "CUSTOMER_HK", CAST("CUSTOMER_HASHDIFF" AS BLOB) AS "CUSTOMER_HASHDIFF", CAST("NAME" AS VARCHAR) AS "NAME", CAST("CITY" AS VARCHAR) AS "CITY", CAST("LOAD_DTS" AS TIMESTAMPTZ) AS "LOAD_DTS", CAST("RECORD_SOURCE" AS VARCHAR) AS "RECORD_SOURCE", "__priority" FROM "_hv_raw_sat_customer" WHERE "CUSTOMER_HK" IS NOT NULL;

-- sat_customer: null load timestamp or record source
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_customer" WHERE "LOAD_DTS" IS NULL OR NULLIF(TRIM("RECORD_SOURCE"), '') IS NULL) THEN error('sat_customer: null load timestamp or record source') ELSE 'ok' END;
-- sat_customer: orphan key CUSTOMER_HK -> hub_customer
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_customer" s WHERE NOT EXISTS (SELECT 1 FROM "hub_customer" p WHERE p."CUSTOMER_HK" = s."CUSTOMER_HK")) THEN error('sat_customer: orphan key CUSTOMER_HK -> hub_customer') ELSE 'ok' END;
-- sat_customer: null hashdiff
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_customer" WHERE "CUSTOMER_HASHDIFF" IS NULL) THEN error('sat_customer: null hashdiff') ELSE 'ok' END;
-- sat_customer: conflicting states at the same load timestamp
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_HK", "LOAD_DTS" FROM (SELECT DISTINCT "CUSTOMER_HK", "CUSTOMER_HASHDIFF", "NAME", "CITY", "LOAD_DTS", "RECORD_SOURCE" FROM "_hv_input_sat_customer") d GROUP BY "CUSTOMER_HK", "LOAD_DTS" HAVING COUNT(*) > 1) THEN error('sat_customer: conflicting states at the same load timestamp') ELSE 'ok' END;
-- sat_customer: late-arriving change requires explicit reconciliation
SELECT CASE WHEN EXISTS (
SELECT 1 FROM "_hv_input_sat_customer" s
WHERE s."LOAD_DTS" <= (SELECT MAX(t."LOAD_DTS") FROM "sat_customer" t WHERE t."CUSTOMER_HK" = s."CUSTOMER_HK")
AND s."CUSTOMER_HASHDIFF" IS DISTINCT FROM (
  SELECT t."CUSTOMER_HASHDIFF" FROM "sat_customer" t WHERE t."CUSTOMER_HK" = s."CUSTOMER_HK" AND t."LOAD_DTS" <= s."LOAD_DTS"
  ORDER BY t."LOAD_DTS" DESC LIMIT 1
)) THEN error('sat_customer: late-arriving change requires explicit reconciliation') ELSE 'ok' END;

INSERT INTO "sat_customer" ("CUSTOMER_HK", "CUSTOMER_HASHDIFF", "NAME", "CITY", "LOAD_DTS", "RECORD_SOURCE")
WITH latest AS (
  SELECT "CUSTOMER_HK", "CUSTOMER_HASHDIFF" AS "__last_hash", "LOAD_DTS" AS "__last_ldts" FROM "sat_customer"
  QUALIFY ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_HK" ORDER BY "LOAD_DTS" DESC) = 1
), fresh AS (
  SELECT DISTINCT s."CUSTOMER_HK", s."CUSTOMER_HASHDIFF", s."NAME", s."CITY", s."LOAD_DTS", s."RECORD_SOURCE", t."__last_hash"
  FROM "_hv_input_sat_customer" s LEFT JOIN latest t ON s."CUSTOMER_HK" = t."CUSTOMER_HK"
  WHERE t."CUSTOMER_HK" IS NULL OR s."LOAD_DTS" > t."__last_ldts"
), ordered AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_HK" ORDER BY "LOAD_DTS") AS "__seq",
  LAG("CUSTOMER_HASHDIFF") OVER (PARTITION BY "CUSTOMER_HK" ORDER BY "LOAD_DTS") AS "__prev_hash"
  FROM fresh
)
SELECT "CUSTOMER_HK", "CUSTOMER_HASHDIFF", "NAME", "CITY", "LOAD_DTS", "RECORD_SOURCE" FROM ordered
WHERE "CUSTOMER_HASHDIFF" IS DISTINCT FROM CASE WHEN "__seq" = 1 THEN "__last_hash" ELSE "__prev_hash" END;

CREATE TABLE IF NOT EXISTS "sat_order" (
  "CUSTOMER_ORDER_HK" BLOB NOT NULL,
  "ORDER_HASHDIFF" BLOB NOT NULL,
  "AMOUNT" VARCHAR,
  "STATUS" VARCHAR,
  "EFFECTIVE_AT" TIMESTAMPTZ,
  "LOAD_DTS" TIMESTAMPTZ NOT NULL,
  "RECORD_SOURCE" VARCHAR NOT NULL,
  PRIMARY KEY ("CUSTOMER_ORDER_HK", "LOAD_DTS")
);

CREATE OR REPLACE TEMP TABLE "_hv_raw_sat_order" AS SELECT "CUSTOMER_ORDER_HK", "ORDER_HASHDIFF", "AMOUNT", "STATUS", "EFFECTIVE_AT", "LOAD_DTS", "RECORD_SOURCE", 0 AS "__priority" FROM "_hv_stage_erp";
CREATE OR REPLACE TEMP TABLE "_hv_input_sat_order" AS SELECT CAST("CUSTOMER_ORDER_HK" AS BLOB) AS "CUSTOMER_ORDER_HK", CAST("ORDER_HASHDIFF" AS BLOB) AS "ORDER_HASHDIFF", CAST("AMOUNT" AS VARCHAR) AS "AMOUNT", CAST("STATUS" AS VARCHAR) AS "STATUS", CAST("EFFECTIVE_AT" AS TIMESTAMPTZ) AS "EFFECTIVE_AT", CAST("LOAD_DTS" AS TIMESTAMPTZ) AS "LOAD_DTS", CAST("RECORD_SOURCE" AS VARCHAR) AS "RECORD_SOURCE", "__priority" FROM "_hv_raw_sat_order" WHERE "CUSTOMER_ORDER_HK" IS NOT NULL;

-- sat_order: null load timestamp or record source
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_order" WHERE "LOAD_DTS" IS NULL OR NULLIF(TRIM("RECORD_SOURCE"), '') IS NULL) THEN error('sat_order: null load timestamp or record source') ELSE 'ok' END;
-- sat_order: orphan key CUSTOMER_ORDER_HK -> link_customer_order
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_order" s WHERE NOT EXISTS (SELECT 1 FROM "link_customer_order" p WHERE p."CUSTOMER_ORDER_HK" = s."CUSTOMER_ORDER_HK")) THEN error('sat_order: orphan key CUSTOMER_ORDER_HK -> link_customer_order') ELSE 'ok' END;
-- sat_order: null hashdiff
SELECT CASE WHEN EXISTS (SELECT 1 FROM "_hv_input_sat_order" WHERE "ORDER_HASHDIFF" IS NULL) THEN error('sat_order: null hashdiff') ELSE 'ok' END;
-- sat_order: conflicting states at the same load timestamp
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_ORDER_HK", "LOAD_DTS" FROM (SELECT DISTINCT "CUSTOMER_ORDER_HK", "ORDER_HASHDIFF", "AMOUNT", "STATUS", "EFFECTIVE_AT", "LOAD_DTS", "RECORD_SOURCE" FROM "_hv_input_sat_order") d GROUP BY "CUSTOMER_ORDER_HK", "LOAD_DTS" HAVING COUNT(*) > 1) THEN error('sat_order: conflicting states at the same load timestamp') ELSE 'ok' END;
-- sat_order: late-arriving change requires explicit reconciliation
SELECT CASE WHEN EXISTS (
SELECT 1 FROM "_hv_input_sat_order" s
WHERE s."LOAD_DTS" <= (SELECT MAX(t."LOAD_DTS") FROM "sat_order" t WHERE t."CUSTOMER_ORDER_HK" = s."CUSTOMER_ORDER_HK")
AND s."ORDER_HASHDIFF" IS DISTINCT FROM (
  SELECT t."ORDER_HASHDIFF" FROM "sat_order" t WHERE t."CUSTOMER_ORDER_HK" = s."CUSTOMER_ORDER_HK" AND t."LOAD_DTS" <= s."LOAD_DTS"
  ORDER BY t."LOAD_DTS" DESC LIMIT 1
)) THEN error('sat_order: late-arriving change requires explicit reconciliation') ELSE 'ok' END;

INSERT INTO "sat_order" ("CUSTOMER_ORDER_HK", "ORDER_HASHDIFF", "AMOUNT", "STATUS", "EFFECTIVE_AT", "LOAD_DTS", "RECORD_SOURCE")
WITH latest AS (
  SELECT "CUSTOMER_ORDER_HK", "ORDER_HASHDIFF" AS "__last_hash", "LOAD_DTS" AS "__last_ldts" FROM "sat_order"
  QUALIFY ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_ORDER_HK" ORDER BY "LOAD_DTS" DESC) = 1
), fresh AS (
  SELECT DISTINCT s."CUSTOMER_ORDER_HK", s."ORDER_HASHDIFF", s."AMOUNT", s."STATUS", s."EFFECTIVE_AT", s."LOAD_DTS", s."RECORD_SOURCE", t."__last_hash"
  FROM "_hv_input_sat_order" s LEFT JOIN latest t ON s."CUSTOMER_ORDER_HK" = t."CUSTOMER_ORDER_HK"
  WHERE t."CUSTOMER_ORDER_HK" IS NULL OR s."LOAD_DTS" > t."__last_ldts"
), ordered AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY "CUSTOMER_ORDER_HK" ORDER BY "LOAD_DTS") AS "__seq",
  LAG("ORDER_HASHDIFF") OVER (PARTITION BY "CUSTOMER_ORDER_HK" ORDER BY "LOAD_DTS") AS "__prev_hash"
  FROM fresh
)
SELECT "CUSTOMER_ORDER_HK", "ORDER_HASHDIFF", "AMOUNT", "STATUS", "EFFECTIVE_AT", "LOAD_DTS", "RECORD_SOURCE" FROM ordered
WHERE "ORDER_HASHDIFF" IS DISTINCT FROM CASE WHEN "__seq" = 1 THEN "__last_hash" ELSE "__prev_hash" END;

-- hub_customer: unique key
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_HK" FROM "hub_customer" GROUP BY "CUSTOMER_HK" HAVING COUNT(*) > 1) THEN error('hub_customer: unique key') ELSE 'ok' END;

-- hub_customer: required columns
SELECT CASE WHEN EXISTS (SELECT 1 FROM "hub_customer" WHERE "CUSTOMER_HK" IS NULL OR "LOAD_DTS" IS NULL OR "RECORD_SOURCE" IS NULL) THEN error('hub_customer: required columns') ELSE 'ok' END;

-- hub_order: unique key
SELECT CASE WHEN EXISTS (SELECT "ORDER_HK" FROM "hub_order" GROUP BY "ORDER_HK" HAVING COUNT(*) > 1) THEN error('hub_order: unique key') ELSE 'ok' END;

-- hub_order: required columns
SELECT CASE WHEN EXISTS (SELECT 1 FROM "hub_order" WHERE "ORDER_HK" IS NULL OR "LOAD_DTS" IS NULL OR "RECORD_SOURCE" IS NULL) THEN error('hub_order: required columns') ELSE 'ok' END;

-- link_customer_order: unique key
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_ORDER_HK" FROM "link_customer_order" GROUP BY "CUSTOMER_ORDER_HK" HAVING COUNT(*) > 1) THEN error('link_customer_order: unique key') ELSE 'ok' END;

-- link_customer_order: required columns
SELECT CASE WHEN EXISTS (SELECT 1 FROM "link_customer_order" WHERE "CUSTOMER_ORDER_HK" IS NULL OR "LOAD_DTS" IS NULL OR "RECORD_SOURCE" IS NULL OR "CUSTOMER_HK" IS NULL OR "ORDER_HK" IS NULL) THEN error('link_customer_order: required columns') ELSE 'ok' END;

-- link_customer_order: relationship CUSTOMER_HK
SELECT CASE WHEN EXISTS (SELECT 1 FROM "link_customer_order" s WHERE NOT EXISTS (SELECT 1 FROM "hub_customer" p WHERE p."CUSTOMER_HK" = s."CUSTOMER_HK")) THEN error('link_customer_order: relationship CUSTOMER_HK') ELSE 'ok' END;

-- link_customer_order: relationship ORDER_HK
SELECT CASE WHEN EXISTS (SELECT 1 FROM "link_customer_order" s WHERE NOT EXISTS (SELECT 1 FROM "hub_order" p WHERE p."ORDER_HK" = s."ORDER_HK")) THEN error('link_customer_order: relationship ORDER_HK') ELSE 'ok' END;

-- sat_customer: unique key
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_HK", "LOAD_DTS" FROM "sat_customer" GROUP BY "CUSTOMER_HK", "LOAD_DTS" HAVING COUNT(*) > 1) THEN error('sat_customer: unique key') ELSE 'ok' END;

-- sat_customer: required columns
SELECT CASE WHEN EXISTS (SELECT 1 FROM "sat_customer" WHERE "CUSTOMER_HK" IS NULL OR "LOAD_DTS" IS NULL OR "RECORD_SOURCE" IS NULL OR "CUSTOMER_HASHDIFF" IS NULL) THEN error('sat_customer: required columns') ELSE 'ok' END;

-- sat_customer: relationship CUSTOMER_HK
SELECT CASE WHEN EXISTS (SELECT 1 FROM "sat_customer" s WHERE NOT EXISTS (SELECT 1 FROM "hub_customer" p WHERE p."CUSTOMER_HK" = s."CUSTOMER_HK")) THEN error('sat_customer: relationship CUSTOMER_HK') ELSE 'ok' END;

-- sat_customer: consecutive change history
SELECT CASE WHEN EXISTS (SELECT 1 FROM (SELECT "CUSTOMER_HASHDIFF", LAG("CUSTOMER_HASHDIFF") OVER (PARTITION BY "CUSTOMER_HK" ORDER BY "LOAD_DTS") AS "__prev" FROM "sat_customer") s WHERE "CUSTOMER_HASHDIFF" = "__prev") THEN error('sat_customer: consecutive change history') ELSE 'ok' END;

-- sat_order: unique key
SELECT CASE WHEN EXISTS (SELECT "CUSTOMER_ORDER_HK", "LOAD_DTS" FROM "sat_order" GROUP BY "CUSTOMER_ORDER_HK", "LOAD_DTS" HAVING COUNT(*) > 1) THEN error('sat_order: unique key') ELSE 'ok' END;

-- sat_order: required columns
SELECT CASE WHEN EXISTS (SELECT 1 FROM "sat_order" WHERE "CUSTOMER_ORDER_HK" IS NULL OR "LOAD_DTS" IS NULL OR "RECORD_SOURCE" IS NULL OR "ORDER_HASHDIFF" IS NULL) THEN error('sat_order: required columns') ELSE 'ok' END;

-- sat_order: relationship CUSTOMER_ORDER_HK
SELECT CASE WHEN EXISTS (SELECT 1 FROM "sat_order" s WHERE NOT EXISTS (SELECT 1 FROM "link_customer_order" p WHERE p."CUSTOMER_ORDER_HK" = s."CUSTOMER_ORDER_HK")) THEN error('sat_order: relationship CUSTOMER_ORDER_HK') ELSE 'ok' END;

-- sat_order: consecutive change history
SELECT CASE WHEN EXISTS (SELECT 1 FROM (SELECT "ORDER_HASHDIFF", LAG("ORDER_HASHDIFF") OVER (PARTITION BY "CUSTOMER_ORDER_HK" ORDER BY "LOAD_DTS") AS "__prev" FROM "sat_order") s WHERE "ORDER_HASHDIFF" = "__prev") THEN error('sat_order: consecutive change history') ELSE 'ok' END;

COMMIT;
