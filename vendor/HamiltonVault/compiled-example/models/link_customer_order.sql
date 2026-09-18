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
