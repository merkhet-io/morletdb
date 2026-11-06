# B3.15, attempt 1: not admissible

Run 2026-11-06 10:23–10:39 with the frozen harness, all 24 loads correct. The start condition of Amendment B3.15 was
not met: the runner's wait for a quiet machine (1-minute load average below 3, no foreign process above half a core)
timed out after 30 minutes and started anyway, at a load average of 5.66 with mediaanalysisd (about 90% of a core),
WindowServer (85%), Safari and the camera service active (`b315-questdb/b315.start`, `machine/`). Decided before
any result was looked at: this attempt is kept for the record, is not B3.15's result, and B3.15 is run again on a
quiet machine; that run is the result whatever it shows. The runner now refuses to start instead of timing out.
