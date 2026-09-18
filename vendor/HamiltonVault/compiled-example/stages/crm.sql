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
