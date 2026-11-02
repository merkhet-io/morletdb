# A real Claude Code session on the machine demo

Recorded on 2026-11-02 with Claude Code in headless mode (`claude -p`), model `claude-opus-5-5`, in
`examples/machine-demo` with only the `morletdb` MCP server allowed (no shell, file or web
tools). One unattended run, not edited: 16 turns, 72 s, USD 0.49. Tool results are
shortened here; Claude's text is verbatim.

Run 2, after capping `trend` at 300 points. Its first `trend` answer (67,877 characters, pretty-printed JSON) still exceeded the limit; `trend` then returned its table as CSV within about 30,000 characters. [Run 1](2026-11-02-bearing-onset-run1.md), [run 3](2026-11-02-bearing-onset-run3.md).

## Prompt

> Bearing 1 on this test rig failed at the end of the test with an outer-race fault. From the vibration recordings, find when it first started to change, describe how it changed, and check whether the other three bearings show anything similar. Cite the snapshots you base this on.

## Tool calls

1. `morletdb.list_collections()`  
   → public.bearings: 3936 recordings, 80609280 samples

2. `morletdb.coverage(collection="public.bearings", group_by="bearing")`  
   → bearing 1: 984 recordings, snapshots, every 600 s, 0 gaps; bearing 2: 984 recordings, snapshots, every 600 s, 0 gaps; bearing 3: 984 recordings, snapshots, every 600 s, 0 gaps; bearing 4: 984 recordings, snapshots, every 600 s, 0 gaps

3. `morletdb.list_recordings(collection="public.bearings", include_metadata=true, limit=2)`  
   → 3936 recordings; first: B1.20040212T103239, B1.20040212T104239

4. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"1\"}", max_points=300, bands="200-280,450-500,1000-3000,3000-5000,5000-10000")`  
   → Error: result (67,877 characters across 3,689 lines) exceeds maximum allowed tokens. Output has been saved to <local file>

5. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"1\"}", max_points=80, bands="200-280,3000-5000")`  
   → 80 points; first sustained change: rms 2004-02-16T07:52, kurtosis 2004-02-17T06:42, crest_factor none, 200-280 Hz 2004-02-19T00:12, 3000-5000 Hz 2004-02-16T05:42

*Claude:* Bearing 1 changes around early 16 Feb. I'm now narrowing that down to individual snapshots, and after that I'll compare the other three bearings.

6. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"1\"}", max_points=60, bands="3000-5000", start_utc="2004-02-15T22:00:00Z", end_utc="2004-02-16T08:00:00Z", baseline_fraction=0.3)`  
   → 60 points; first sustained change: rms none, kurtosis none, crest_factor none, 3000-5000 Hz 2004-02-16T05:22

7. `morletdb.compare_intervals(collection="public.bearings", recording_id="B1.20040216T032239", start_sample=0, end_sample=20480, reference_recording_id="B1.20040216T013239", reference_start_sample=0, reference_end_sample=20480, bands="0-1000,1000-2000,2000-3000,3000-4000,4000-5000,5000-7000,7000-10000")`  
   → 0.65 dB above reference; excess by band: 0-1000 Hz 0.00, 1000-2000 Hz 0.06, 2000-3000 Hz 0.08, 3000-4000 Hz 0.06, 4000-5000 Hz 0.78, 5000-7000 Hz 0.03, 7000-10000 Hz 0.00

8. `morletdb.compare_intervals(collection="public.bearings", recording_id="B1.20040217T084239", start_sample=0, end_sample=20480, reference_recording_id="B1.20040213T112239", reference_start_sample=0, reference_end_sample=20480, bands="0-1000,1000-2000,2000-3000,3000-4000,4000-5000,5000-7000,7000-10000")`  
   → 6.74 dB above reference; excess by band: 0-1000 Hz 0.01, 1000-2000 Hz 0.03, 2000-3000 Hz 0.01, 3000-4000 Hz 0.15, 4000-5000 Hz 0.72, 5000-7000 Hz 0.06, 7000-10000 Hz 0.01

*Claude:* Bearing 1 changes as a step at 03:22 on 16 Feb, with the new energy mostly in 4–5 kHz. Now checking bearings 2–4 the same way.

9. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"2\"}", max_points=40, bands="4000-5000")`  
   → 40 points; first sustained change: rms 2004-02-18T17:42, kurtosis none, crest_factor none, 4000-5000 Hz none

10. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"3\"}", max_points=40, bands="4000-5000")`  
   → 40 points; first sustained change: rms 2004-02-18T17:42, kurtosis none, crest_factor none, 4000-5000 Hz none

11. `morletdb.trend(collection="public.bearings", metadata_filter="{\"bearing\": \"4\"}", max_points=40, bands="4000-5000")`  
   → 40 points; first sustained change: rms 2004-02-18T09:22, kurtosis none, crest_factor none, 4000-5000 Hz 2004-02-16T23:42

12. `morletdb.compare_intervals(collection="public.bearings", recording_id="B4.20040217T081239", start_sample=0, end_sample=20480, reference_recording_id="B4.20040213T114239", reference_start_sample=0, reference_end_sample=20480, bands="0-1000,1000-2000,2000-3000,3000-4000,4000-5000,5000-7000,7000-10000")`  
   → 1.25 dB above reference; excess by band: 0-1000 Hz 0.00, 1000-2000 Hz 0.10, 2000-3000 Hz 0.09, 3000-4000 Hz 0.39, 4000-5000 Hz 0.37, 5000-7000 Hz 0.00, 7000-10000 Hz 0.04

13. `morletdb.compare_intervals(collection="public.bearings", recording_id="B2.20040218T220239", start_sample=0, end_sample=20480, reference_recording_id="B2.20040213T114239", reference_start_sample=0, reference_end_sample=20480, bands="0-1000,1000-2000,2000-3000,3000-4000,4000-5000,5000-7000,7000-10000")`  
   → 2.73 dB above reference; excess by band: 0-1000 Hz 0.86, 1000-2000 Hz 0.07, 2000-3000 Hz 0.01, 3000-4000 Hz 0.00, 4000-5000 Hz 0.06, 5000-7000 Hz 0.00, 7000-10000 Hz 0.00

14. `morletdb.compare_intervals(collection="public.bearings", recording_id="B3.20040218T220239", start_sample=0, end_sample=20480, reference_recording_id="B3.20040213T155239", reference_start_sample=0, reference_end_sample=20480, bands="0-1000,1000-2000,2000-3000,3000-4000,4000-5000,5000-7000,7000-10000")`  
   → 3.06 dB above reference; excess by band: 0-1000 Hz 0.99, 1000-2000 Hz 0.01, 2000-3000 Hz 0.00, 3000-4000 Hz 0.00, 4000-5000 Hz 0.00, 5000-7000 Hz 0.00, 7000-10000 Hz 0.00

## Answer

Bearing 1's first lasting change is at **16 Feb 2004, 03:22:39 UTC**, about 3 days before the end of the test. It showed up as extra energy around 4–5 kHz, not as larger peaks. Bearing 4 changes later in the same band but much more weakly, at the same moment bearing 1 got sharply worse. Bearings 2 and 3 change only in the last ~12 hours, and in a different way.

All the snapshots are in `public.bearings`. Each one is 1.024 s (20,480 samples at 20 kHz), taken every 10 min from 12 Feb 10:32 to 19 Feb 06:22, and I cite each as the full range `0..20480`.

## Bearing 1: when it started

- **Before:** From 12 Feb to 03:12 on 16 Feb, the level stays flat: RMS about 0.075–0.080, 3–5 kHz band power about 0.0026, kurtosis about 3.3.
- **First change:** `B1.20040216T032239` is the first snapshot of a step that lasts. The 3–5 kHz band power goes from about 0.0030 to 0.0039 and never returns to baseline afterwards. RMS goes from about 0.078 to 0.086.
- **Check against an earlier snapshot:** I compared it with `B1.20040216T013239`. 78% of the new power is in 4–5 kHz (+1.8 dB there). Below 1 kHz there is no change.
- **What did not change:** Kurtosis (3.45) and crest factor (5.1) stay normal. So at first it is an energy rise in that band, not visible impacts.

## Bearing 1: how it got worse

1. **16 Feb 03:22 to 17 Feb 06:42:** a steady climb. RMS rises from 0.086 to 0.109, and 3–5 kHz power from 0.004 to 0.0076.
2. **Jump at about 17 Feb 08:42:** in `B1.20040217T084239`, RMS is 0.164 and kurtosis is 5.9. Compared with the quiet `B1.20040213T112239`, the total is +6.7 dB and 4–5 kHz is +10.7 dB, holding 72% of the new power. This is when the signal becomes impulsive.
3. **17 Feb about 15:00 to 18 Feb 03:00:** it partly settles back (RMS about 0.10–0.11). The surface may have been wearing smooth for a while; I haven't checked that.
4. **18 Feb 05:32 onwards:** a second rise. `B1.20040219T042239` is the worst snapshot: RMS 0.416, peak 2.8, kurtosis 8.3.

The final snapshot of every bearing (`B*.20040219T062239`) is almost zero (RMS about 0.001). The rig or the logging had stopped, so I left those out.

## The other three bearings

| Bearing | What changes | When | Same as bearing 1? |
|---|---|---|---|
| **4** | RMS 0.056 → 0.065; 4–5 kHz +3.3 dB; 77% of the new power in 3–5 kHz; kurtosis unchanged (3.2) | from `B4.20040217T081239`, against `B4.20040213T114239` | **Same band, much weaker** (+1.25 dB total vs bearing 1's +6.7 dB) |
| **2** | RMS 0.095 → 0.130; 86% of the new power **below 1 kHz**; 4–5 kHz only +1.5 dB | late 18 Feb (`B2.20040218T220239`, against `B2.20040213T114239`) | **No:** a low-frequency change |
| **3** | RMS 0.100 → 0.143; 99% of the new power **below 1 kHz** (+8.5 dB); 4–5 kHz unchanged; kurtosis drops | late 18 Feb (`B3.20040218T220239`, against `B3.20040213T155239`) | **No:** a low-frequency change |

Bearing 4's rise starts at the same time as bearing 1's sharp jump (17 Feb, about 08:12–08:42). It also sits in the same band and has no impacts of its own. That fits bearing 1's vibration reaching bearing 4's sensor through the shaft or housing better than a second fault in bearing 4. Bearings 2 and 3 only change in the last hours, below 1 kHz, which looks like the whole shaft vibrating more as bearing 1 failed, not a bearing fault of their own. Both explanations are inferences from timing and frequency content, not proven.

## What I couldn't check

- **Outer race:** These tools can't produce an envelope spectrum, so I couldn't check for the outer-race defect frequency (roughly 236 Hz for this bearing at 2000 rpm). The data shows that bearing 1 changed and when, but doesn't by itself confirm an outer-race fault. A 200–280 Hz band trend on bearing 1 only rose in the last day (from 19 Feb 00:12), and a plain spectrum isn't a good test for that anyway.
- **Exact onset:** The onset is sharp to one snapshot only in the 3–5 kHz band. RMS crosses its automatic change threshold (5 robust standard deviations above the first-day baseline) later, at about 07:52 on 16 Feb.
