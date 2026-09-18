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
