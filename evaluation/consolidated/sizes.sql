-- Per-structure sizes of an evaluation database (read-only; run after E1). One CSV row per relation:
-- relation, role, bytes (main fork + TOAST, i.e. pg_table_size for tables, pg_relation_size for indexes).
-- Roles: raw = raw chunks and their key; envelopes = envelope table and key; probe = product probe table and
-- key; probe_index = the HNSW index; other = structures left from earlier studies (not used by the product).
\pset format csv
\pset tuples_only off
SELECT c.relname AS relation,
  CASE WHEN c.relname IN ('r2_chunks', 'r2_chunks_pkey') THEN 'raw'
       WHEN c.relname IN ('r2_envelopes', 'r2_envelopes_pkey') THEN 'envelopes'
       WHEN c.relname IN ('r2_probes_4096', 'r2_probes_4096_pkey') THEN 'probe'
       WHEN c.relname = 'r2_probes_4096_v_idx' THEN 'probe_index'
       ELSE 'other' END AS role,
  CASE WHEN c.relkind = 'i' THEN pg_relation_size(c.oid) ELSE pg_table_size(c.oid) END AS bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname LIKE 'r2%' AND c.relkind IN ('r', 'i')
ORDER BY 2, 1;
