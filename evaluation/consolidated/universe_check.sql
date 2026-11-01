-- E1 search universe (run per database after E1; read-only): every stored 4096-sample envelope has its
-- probe row, no other window size exists, each recording's windows are a gap-free prefix with no missing
-- tail window, and the envelope table saw no writes after the initial load (statistics counters).
SELECT current_database() AS db,
  (SELECT count(*) FROM public.r2_envelopes WHERE window_size = 4096) AS envelopes,
  (SELECT count(*) FROM public.r2_probes_4096) AS probe_rows,
  (SELECT count(*) FROM public.r2_envelopes e WHERE e.window_size = 4096 AND NOT EXISTS
     (SELECT 1 FROM public.r2_probes_4096 p WHERE p.recording_id = e.recording_id AND p.start_sample = e.start_sample)) AS envelopes_without_probe_row,
  (SELECT count(*) FROM public.r2_envelopes WHERE window_size <> 4096) AS other_window_sizes,
  (SELECT n_tup_ins + n_tup_upd + n_tup_del FROM pg_stat_user_tables WHERE relname = 'r2_envelopes') AS envelope_writes_since_stats_reset;
SELECT current_database() AS db, count(*) AS recordings, sum((min_s <> 0)::int) AS not_starting_at_zero,
  sum((n <> (max_s - min_s) / 2048 + 1)::int) AS with_gaps, sum((max_s + 4096 + 2048 <= checkpoint_end)::int) AS missing_tail_window
FROM (SELECT e.recording_id, count(*) n, min(start_sample) min_s, max(start_sample) max_s,
             max((r.checkpoint->>'end')::bigint) checkpoint_end
      FROM public.r2_envelopes e JOIN public.r2 r USING (recording_id) WHERE window_size = 4096 GROUP BY 1) x;
