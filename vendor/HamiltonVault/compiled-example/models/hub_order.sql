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
