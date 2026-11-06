# B3.15, attempt 2: not admissible

Run 2026-11-06 11:58–15:26 with the frozen harness after the start condition was met (load average 2.92,
`b315-questdb/b315.start`); all 24 loads correct. At 12:04:31, during round 1, the Mac entered sleep on a closed lid
while running on battery (`power_log_excerpt.txt`, from `pmset -g log`), and slept with brief dark wakes 21 times
until about 15:20. `caffeinate -ims` does not prevent lid-closed sleep on battery. Rounds 2 and 3 therefore ran across
sleep and wake, outside the protocol's machine condition (lid open, AC power). Decided from the power log alone,
before any result was looked at: this attempt is kept for the record, is not B3.15's result, and B3.15 is run again;
that run is the result whatever it shows.

Also recorded: a first arming at 11:57 stopped before any load because the runner's start record exited under
`set -o pipefail` (fixed in 18a2b03, no measurement change).

The runner now also checks before every load that the Mac is on AC power with the lid open, and stops the session
otherwise.
