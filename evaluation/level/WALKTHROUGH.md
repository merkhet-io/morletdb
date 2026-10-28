# Walkthrough: one level track

From `sql_check.py`'s synthetic collection `lvl` (pg_morlet SQL 0.3.1 from this branch,
database `morlet_level_check`). Machine M1, mode a. The ln(std) level drifts slowly, then
steps up by 0.4 at e30. The `zero` episode is an all-zero recording. `same-time` starts at
the same time as e12. Mode-b episodes and machine M2 are left out of the track.

```sql
SELECT episode, occurrence->>'episode' AS ep, round(hours_since_previous::numeric,1) AS gap_h,
  round(observed::numeric,3) AS std, round(exp(predicted_level)::numeric,3) AS predicted,
  round(exp(level)::numeric,3) AS level_std,
  round(exp(level-1.96*sqrt(level_var))::numeric,3) AS low,
  round(exp(level+1.96*sqrt(level_var))::numeric,3) AS high,
  round(standardized_innovation::numeric,1) AS z, status
FROM morlet_level_track('lvl',(SELECT segment_id FROM lvl_ctx_segments WHERE recording_id='e39/V'),0.004,0.01)
ORDER BY episode;
```

```text
 episode |    ep     | gap_h |  std  | predicted | level_std |  low  | high  |  z   |    status     
---------+-----------+-------+-------+-----------+-----------+-------+-------+------+---------------
       1 | e0        |       | 0.764 |           |     0.764 | 0.628 | 0.930 |      | initialized
       2 | e1        |   0.5 | 1.074 |     0.764 |     0.920 | 0.796 | 1.063 |  2.3 | ok
       3 | e3        |   1.5 | 1.082 |     0.920 |     1.003 | 0.869 | 1.158 |  1.1 | ok
       4 | e4        |   1.0 | 0.857 |     1.003 |     0.930 | 0.811 | 1.065 | -1.1 | ok
       5 | e6        |   8.0 | 0.879 |     0.930 |     0.890 | 0.748 | 1.058 | -0.3 | ok
       6 | e7        |   1.0 | 0.964 |     0.890 |     0.929 | 0.804 | 1.074 |  0.5 | ok
       7 | e9        |   1.5 | 0.932 |     0.929 |     0.931 | 0.807 | 1.074 |  0.0 | ok
       8 | e10       |   2.0 | 1.023 |     0.931 |     0.982 | 0.847 | 1.139 |  0.6 | ok
       9 | e12       |   2.0 | 1.083 |     0.982 |     1.039 | 0.895 | 1.206 |  0.6 | ok
      10 | same-time |   0.0 | 1.048 |     1.039 |     1.042 | 0.926 | 1.174 |  0.1 | ok
      11 | e13       |   2.0 | 0.988 |     1.042 |     1.013 | 0.877 | 1.169 | -0.4 | ok
      12 | e15       |   1.0 | 0.968 |     1.013 |     0.991 | 0.865 | 1.136 | -0.3 | ok
      13 | e16       |   2.0 | 1.061 |     0.991 |     1.029 | 0.889 | 1.192 |  0.4 | ok
      14 | e18       |   8.0 | 0.975 |     1.029 |     0.987 | 0.829 | 1.174 | -0.2 | ok
      15 | e19       |   7.0 | 0.994 |     0.987 |     0.992 | 0.834 | 1.180 |  0.0 | ok
      16 | e21       |   1.5 | 1.068 |     0.992 |     1.035 | 0.892 | 1.202 |  0.5 | ok
      17 | e22       |   7.0 | 0.891 |     1.035 |     0.922 | 0.776 | 1.095 | -0.7 | ok
      18 | e24       |   2.0 | 0.964 |     0.922 |     0.947 | 0.813 | 1.104 |  0.3 | ok
      19 | e25       |   1.0 | 1.086 |     0.947 |     1.015 | 0.883 | 1.166 |  1.0 | ok
      20 | zero      |   0.3 | 0.000 |     1.015 |           |       |       |      | invalid_value
      21 | e27       |   2.0 | 0.923 |     1.015 |     0.962 | 0.830 | 1.115 | -0.6 | ok
      22 | e28       |   1.0 | 0.803 |     0.962 |     0.880 | 0.767 | 1.010 | -1.3 | ok
      23 | e30       |   2.5 | 1.457 |     0.880 |     1.190 | 1.023 | 1.385 |  3.2 | ok
      24 | e31       |   7.0 | 1.522 |     1.190 |     1.439 | 1.211 | 1.710 |  1.2 | ok
      25 | e33       |   3.0 | 1.334 |     1.439 |     1.369 | 1.167 | 1.606 | -0.4 | ok
      26 | e34       |   7.0 | 1.509 |     1.369 |     1.476 | 1.242 | 1.755 |  0.5 | ok
      27 | e36       |   1.5 | 1.589 |     1.476 |     1.541 | 1.327 | 1.788 |  0.5 | ok
      28 | e37       |   1.0 | 1.398 |     1.541 |     1.468 | 1.279 | 1.685 | -0.7 | ok
      29 | e39       |   8.0 | 1.557 |     1.468 |     1.538 | 1.292 | 1.830 |  0.3 | ok
(29 rows)
```

- **Observations vs level:**
  - `std` is each episode's observed value;
  - `level_std` = exp(level) is the filtered geometric level;
  - `low`/`high` is its model-based 95% interval.
- **Gaps widen the interval.** After the 8 h gap at episode 5 the interval is wider than after
  the 1 h gaps around it.
- **Invalid episode.** `zero` has std 0, so ln is undefined: the row shows the prediction but
  no level, and the next episode is predicted from the last valid one.
- **Equal start times.** `same-time` follows e12 with a 0 h gap, so no process variance is
  added.
- **The step at e30 is a residual first.** z = 3.2 when it arrives, while the level moves only
  part of the way (0.880 → 1.190). It then follows over the next episodes (1.439, 1.476,
  1.541) as the innovations stay mostly positive. A single large z with no follow-up would
  instead be a short-lived disturbance.
- **Parameters.** q = 0.004 per hour and r = 0.01 are this example's; real tracks need
  variances calibrated on their own development history.
