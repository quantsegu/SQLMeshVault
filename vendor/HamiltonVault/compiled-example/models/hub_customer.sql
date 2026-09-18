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
