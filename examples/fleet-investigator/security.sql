-- Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
-- SPDX-License-Identifier: Apache-2.0
--
-- One set of access rules for the graph (Apache AGE) and the recordings (pg_morlet): which roles see which sites.
-- Applied by load.py as the database owner; idempotent. What holds and what does not is measured in CHECKS.md
-- (check 3); in short:
--   - Recordings: an ordinary RLS policy on the recordings table, made binding for every companion table by
--     morlet_enable_row_security (docs/access-control.md).
--   - Graph: AGE keeps every vertex and edge label in an ordinary table of the graph's schema, so the same policy
--     goes on each label table, including the parents _ag_label_vertex and _ag_label_edge. Fixed-length Cypher
--     patterns and direct SQL then return only visible rows.
--   - AGE 1.7.0 (apache/age#2309) applies row-level security to Cypher's own scans and writes and to
--     startNode/endNode. Variable-length paths, vertex_stats and graph_stats read AGE's cached global graph, which
--     1.7.0 still loads without row-level security (fixed on AGE master by apache/age#2561, in no PG17 release yet).
--     fleet.restrict_age_functions revokes these from PUBLIC, and startNode/endNode as well (defence in depth).
--   - AGE creates a table for each new label without firing event triggers, so a new label has no policy until
--     fleet.secure_graph runs again. Restricted roles get SELECT only from fleet.secure_graph, so a new label is
--     refused to them (fails closed) until then; fleet.graph_security_gaps lists such tables.

CREATE SCHEMA IF NOT EXISTS fleet;

-- Which (group) role sees which site. '*' marks enterprise-level nodes (lots, failure modes, ...).
CREATE TABLE IF NOT EXISTS fleet.site_access (
  role name NOT NULL,
  site text NOT NULL,
  PRIMARY KEY (role, site)
);

-- The rule itself, for both stores. SECURITY INVOKER: current_user is the querying role.
CREATE OR REPLACE FUNCTION fleet.site_visible(site text) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER SET search_path = pg_catalog AS $$
  SELECT EXISTS (SELECT 1 FROM fleet.site_access a
                 WHERE a.site = $1 AND pg_has_role(current_user, a.role, 'MEMBER'))
$$;

-- The same rule on a graph element's properties ({"site": ...}). A missing site property hides the element.
CREATE OR REPLACE FUNCTION fleet.graph_site_visible(props ag_catalog.agtype) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER SET search_path = pg_catalog AS $$
  SELECT fleet.site_visible(ag_catalog.agtype_access_operator(props, '"site"'::ag_catalog.agtype)::text)
$$;

-- Enables RLS with the site rule on every label table of the graph and grants SELECT on exactly those tables to
-- the reader roles. Returns the number of label tables. Run it again after any new label is created.
CREATE OR REPLACE FUNCTION fleet.secure_graph(graph_name name, readers name[]) RETURNS integer
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE r record; reader name; n integer := 0;
BEGIN
  FOR r IN SELECT l.relation::regclass AS rel FROM ag_catalog.ag_label l
           JOIN ag_catalog.ag_graph g ON g.graphid = l.graph WHERE g.name = graph_name ORDER BY l.id LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', r.rel);
    IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = r.rel AND polname = 'site_scope') THEN
      EXECUTE format('CREATE POLICY site_scope ON %s USING (fleet.graph_site_visible(properties)) '
                     'WITH CHECK (fleet.graph_site_visible(properties))', r.rel);
    END IF;
    FOREACH reader IN ARRAY readers LOOP
      EXECUTE format('GRANT SELECT ON %s TO %I', r.rel, reader);
    END LOOP;
    n := n + 1;
  END LOOP;
  FOREACH reader IN ARRAY readers LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I, ag_catalog TO %I', graph_name, reader);
  END LOOP;
  RETURN n;
END $$;

-- AGE functions that read vertices and edges through AGE's cached global graph, outside the query's table scans and
-- so outside row-level security in AGE 1.7.0: age_vle (variable-length paths), age_vertex_stats, age_graph_stats
-- (counts of all vertices and edges), age_delete_global_graphs (the cache itself). age_startnode/age_endnode read by
-- id; 1.7.0 checks row-level security there, and they stay restricted as defence in depth (CHECKS.md, check 3).
CREATE OR REPLACE FUNCTION fleet.age_bypass_functions() RETURNS SETOF regprocedure
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'ag_catalog'
    AND p.proname IN ('age_vle', 'age_startnode', 'age_endnode', 'age_vertex_stats', 'age_graph_stats',
                      'age_delete_global_graphs')
$$;

CREATE OR REPLACE FUNCTION fleet.restrict_age_functions() RETURNS integer
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE f regprocedure; n integer := 0;
BEGIN
  FOR f IN SELECT * FROM fleet.age_bypass_functions() LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;

-- What would let a role around the rule: label tables without RLS or without the site policy, and login roles
-- (other than superusers) that may execute a restricted AGE function. Empty when the rule holds as tested.
CREATE OR REPLACE FUNCTION fleet.graph_security_gaps(graph_name name) RETURNS TABLE(object text, problem text)
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT l.relation::regclass::text, 'row-level security is not enabled'
  FROM ag_catalog.ag_label l JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
  JOIN pg_class c ON c.oid = l.relation WHERE g.name = graph_name AND NOT c.relrowsecurity
  UNION ALL
  SELECT l.relation::regclass::text, 'no site_scope policy'
  FROM ag_catalog.ag_label l JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
  WHERE g.name = graph_name AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = l.relation
                                       AND p.polname = 'site_scope')
  UNION ALL
  SELECT format('%s: %s', r.rolname, f), 'may execute a restricted AGE function (CHECKS.md, check 3)'
  FROM pg_roles r CROSS JOIN fleet.age_bypass_functions() f
  WHERE r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
    AND has_function_privilege(r.oid, f, 'EXECUTE')
  UNION ALL
  SELECT r.rolname::text, 'can bypass row-level security (BYPASSRLS)'
  FROM pg_roles r WHERE r.rolcanlogin AND NOT r.rolsuper AND r.rolbypassrls
$$;

-- The recordings: the same rule on the recordings table; morlet_enable_row_security makes every companion table
-- (chunks, envelopes, context, labels, episodes, ...) follow it.
CREATE OR REPLACE FUNCTION fleet.secure_collection(collection regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', collection);
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = collection AND polname = 'site_scope') THEN
    EXECUTE format('CREATE POLICY site_scope ON %s USING (fleet.site_visible(metadata->>''site'')) '
                   'WITH CHECK (fleet.site_visible(metadata->>''site''))', collection);
  END IF;
  PERFORM public.morlet_enable_row_security(collection);
END $$;
