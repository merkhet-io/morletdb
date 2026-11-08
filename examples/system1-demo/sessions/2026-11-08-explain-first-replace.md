# A real Claude Code session: explaining a System 1 decision

Recorded on 2026-11-08 with Claude Code in headless mode (`claude -p`), model `claude-opus-5-5`, in
`examples/system1-demo` with only the `morletdb` MCP server allowed (no shell, file or web tools), connected as a
read-only role. One unattended run, not edited: 28 turns, 88 s, USD 0.68, 26 MorletDB tool calls (one error:
`trend` needs recordings with a start time, which the milling cuts do not have). Tool results are shortened to
400 characters and the loader's role name is replaced; Claude's text is verbatim.

Before the session, `worker.py --case 10` had logged one Jev decision per cut (README). The flank wear VB is not in
the database; Claude did not know it.

**Read with this:** Claude judged the decision "justified, and if anything one cut late" by analogy with insert c04,
whose cuts carry the label `not_sharp`. In this demo that label means VB above 0.2 mm (C1's definition of a sharp
insert), not the 0.3 mm replacement limit the model was asked about. The measured VB at cut 5 was 0.25 mm: one cut
early by the 0.3 mm rule, already past "sharp" by the 0.2 mm label. Claude's own caveat says the analogy is not
ground truth.

## Prompt

> A fast decision model watches this milling machine and decides after every cut whether the cutting insert should be replaced; each decision is logged in MorletDB. Look at the decisions for the insert whose recordings start with c10. Explain the first replace_insert decision from the recordings: which sensors and values drove it, how this cut compares with earlier cuts of the same setting and with the cuts just before it, and whether the decision looks justified or premature. Cite the recordings you base this on.

## Tool calls

1. `morletdb.list_collections()`  
   → { "collections": [ { "collection": "public.milling", "recordings": 996, "samples": 9002160, "sample_rates_hz": [ 250.0 ], "search": [] } ] }

2. `morletdb.decisions(collection="public.milling", recording_prefix="c10", limit=100)`  
   → { "collection": "public.milling", "decisions": [ { "answer": "replace_insert", "cut": "case 10 run 10", "decision_id": "fd2285c25620405ca33c93b6af941d3b", "model": "jev-1.14.0", "probability": 0.69, "question": "replace_insert", "state_sha256": "d3f59a7be0115aa0361213054c3cac6bf8c3889fb8eddfaac99686eb4ce32706", "threshold": 0.6, "v": 1, "author": "<loader role>", "decided_at": "2026-11-08T19:37:31.160566+01 …

*Claude:* The first replace is run 5. Its probability is 0.60, exactly at the threshold. Next I'll look at the state the model saw and the current decision state for each sensor.

3. `morletdb.decisions(collection="public.milling", recording_prefix="c10r005", with_state=true, limit=1)`  
   → { "collection": "public.milling", "decisions": [ { "answer": "replace_insert", "cut": "case 10 run 5", "decision_id": "797d01a40f1d408e963a8f39c769a1fc", "model": "jev-1.14.0", "probability": 0.6, "question": "replace_insert", "state": { "channels": { "AE_spindle": { "history_runs": 3, "sensor": "acoustic emission at the spindle", "summary_quantile": 0.333333, "summary_score": 0.305668, "values":  …

4. `morletdb.list_recordings(collection="public.milling", metadata_filter="{\"case\": 10}", include_metadata=true, limit=12)`  
   → { "collection": "public.milling", "total": 60, "offset": 0, "sample_rate_hz": 250.0, "unit": "V", "recordings": [ { "recording_id": "c10r001-ac", "samples": 9000, "duration_s": 36.0, "start_utc": null, "end_utc": null, "channel": "smcAC", "metadata": { "case": 10 } }, { "recording_id": "c10r001-as", "samples": 9000, "duration_s": 36.0, "start_utc": null, "end_utc": null, "channel": "AE_spindle", " …

5. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-vs", sample=4500, include_occurrence=true, neighbors=5)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 388, "recording_id": "c10r005-vs", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "vib_spindle", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 16, …

6. `morletdb.read_interval(collection="public.milling", recording_id="c10r005-vs", start_sample=0, end_sample=9000, max_points=300)`  
   → { "collection": "public.milling", "recording_id": "c10r005-vs", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "unit": "V", "start_utc": null, "bucket_samples": 60, "min_max": [ [ 0.262451171875, 0.318603515625 ], [ 0.264892578125, 0.333251953125 ], [ 0.262451171875, 0.3271484375 ], [ 0.263671875, 0.321044921875 ], [ 0.260009765625, 0.328369140625 ], [ 0.26123046875, 0.3173828125  …

7. `morletdb.trend(collection="public.milling", metadata_filter="{\"case\": 10}", samples_per_point=9000, max_points=60)`  
   → ERROR: Error executing tool trend: no recordings with a start time match metadata_filter

8. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-dc", sample=4500, include_occurrence=true, neighbors=5)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 385, "recording_id": "c10r005-dc", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "smcDC", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 16, "excl …

9. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-vt", sample=4500, include_occurrence=true, neighbors=5)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 387, "recording_id": "c10r005-vt", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "vib_table", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 16, " …

10. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-ac", sample=4500, include_occurrence=true, neighbors=3)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 386, "recording_id": "c10r005-ac", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "smcAC", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 16, "excl …

11. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-vs", sample=4500, exclude_labels="evaluation,not_sharp", include_occurrence=true, neighbors=5)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 388, "recording_id": "c10r005-vs", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "vib_spindle", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 3,  …

12. `morletdb.decision_state(collection="public.milling", recording_id="c10r005-dc", sample=4500, exclude_labels="evaluation,not_sharp", include_occurrence=true, neighbors=5)`  
   → { "v": 1, "segment": { "collection": "public.milling", "segment_id": 385, "recording_id": "c10r005-dc", "start_sample": 0, "end_sample": 9000, "sample_rate_hz": 250.0, "channel": "smcDC", "unit": "V", "context": { "doc": 1.5, "feed": 0.25, "machine": "MC-510V", "material": 1 }, "occurrence": { "run": "10/5" } }, "history": { "match_keys": [ "doc", "feed", "machine", "material" ], "size": 3, "exclu …

13. `morletdb.list_recordings(collection="public.milling", metadata_filter="{\"case\": 4}", include_metadata=true, limit=6)`  
   → { "collection": "public.milling", "total": 42, "offset": 0, "sample_rate_hz": 250.0, "unit": "V", "recordings": [ { "recording_id": "c04r001-ac", "samples": 9000, "duration_s": 36.0, "start_utc": null, "end_utc": null, "channel": "smcAC", "metadata": { "case": 4 } }, { "recording_id": "c04r001-as", "samples": 9000, "duration_s": 36.0, "start_utc": null, "end_utc": null, "channel": "AE_spindle", "m …

*Claude:* The 3 sharp history runs the model saw are c04r001–003, all from a different insert (case 4) with the same setting. None of them is a c10 run. Now the c10 cuts just before and after:

14. `morletdb.describe_interval(collection="public.milling", recording_id="c10r001-vs", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r001-vs", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 0.2972125922309028, "std": 0.015662461266481915, "rms": 0.2976249950416412, "min": 0.25390625, "max": 0.39794921875, "peak_abs": 0.39794921875, "crest_factor": 1.337082655622799, "kurtosis": 3.22 …

15. `morletdb.describe_interval(collection="public.milling", recording_id="c10r002-vs", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r002-vs", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 0.30121107313368056, "std": 0.032890156178240045, "rms": 0.3030014405110518, "min": 0.25390625, "max": 0.92041015625, "peak_abs": 0.92041015625, "crest_factor": 3.0376428399073188, "kurtosis": 91 …

16. `morletdb.describe_interval(collection="public.milling", recording_id="c10r003-vs", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r003-vs", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 0.2882199435763889, "std": 0.013255606556986401, "rms": 0.28852460376954064, "min": 0.245361328125, "max": 0.347900390625, "peak_abs": 0.347900390625, "crest_factor": 1.2057910697379757, "kurtosi …

17. `morletdb.describe_interval(collection="public.milling", recording_id="c10r004-vs", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r004-vs", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 0.30000691731770834, "std": 0.015674062582542128, "rms": 0.3004160892434287, "min": 0.26123046875, "max": 0.386962890625, "peak_abs": 0.386962890625, "crest_factor": 1.2880897677602146, "kurtosis …

18. `morletdb.describe_interval(collection="public.milling", recording_id="c10r006-vs", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r006-vs", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 0.28564805772569446, "std": 0.027975900840779125, "rms": 0.2870147451095758, "min": 0.247802734375, "max": 1.405029296875, "peak_abs": 1.405029296875, "crest_factor": 4.895320957599552, "kurtosis …

19. `morletdb.describe_interval(collection="public.milling", recording_id="c10r001-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r001-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 4.7335791015625, "std": 1.612952385495156, "rms": 5.000838580540644, "min": 1.318359375, "max": 6.220703125, "peak_abs": 6.220703125, "crest_factor": 1.2439319975665912, "kurtosis": 3.10140325035 …

20. `morletdb.describe_interval(collection="public.milling", recording_id="c10r002-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r002-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 4.917646484375, "std": 1.5203028048756917, "rms": 5.147287398601199, "min": 1.34765625, "max": 6.2646484375, "peak_abs": 6.2646484375, "crest_factor": 1.2170776473842222, "kurtosis": 3.8299331740 …

21. `morletdb.describe_interval(collection="public.milling", recording_id="c10r003-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r003-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 5.548324652777778, "std": 1.7050627334697364, "rms": 5.804407409692133, "min": 1.337890625, "max": 7.060546875, "peak_abs": 7.060546875, "crest_factor": 1.216411319303738, "kurtosis": 4.278834541 …

22. `morletdb.describe_interval(collection="public.milling", recording_id="c10r004-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r004-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 6.1561376953125, "std": 1.906950506410073, "rms": 6.444725871404084, "min": 1.3330078125, "max": 7.71484375, "peak_abs": 7.71484375, "crest_factor": 1.1970786506578286, "kurtosis": 4.457267688289 …

23. `morletdb.describe_interval(collection="public.milling", recording_id="c10r006-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c10r006-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 7.374219292534722, "std": 2.5155919644099316, "rms": 7.791489787312508, "min": 1.34765625, "max": 9.404296875, "peak_abs": 9.404296875, "crest_factor": 1.2069959830164638, "kurtosis": 4.016303807 …

*Claude:* A clear pattern is emerging. One more check: the spindle-current level at which the case-4 insert was first labelled `not_sharp`.

24. `morletdb.describe_interval(collection="public.milling", recording_id="c04r003-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c04r003-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 5.489027777777777, "std": 1.6534568871912891, "rms": 5.732656070532782, "min": 1.318359375, "max": 6.865234375, "peak_abs": 6.865234375, "crest_factor": 1.1975660654559308, "kurtosis": 4.28513371 …

25. `morletdb.describe_interval(collection="public.milling", recording_id="c04r004-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c04r004-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 6.168229166666666, "std": 1.8832564104738203, "rms": 6.4493182399466145, "min": 1.3427734375, "max": 7.6416015625, "peak_abs": 7.6416015625, "crest_factor": 1.184869668109796, "kurtosis": 4.54982 …

26. `morletdb.describe_interval(collection="public.milling", recording_id="c04r005-dc", start_sample=0, end_sample=9000)`  
   → { "collection": "public.milling", "recording_id": "c04r005-dc", "start_sample": 0, "end_sample": 9000, "unit": "V", "sample_rate_hz": 250.0, "start_utc": null, "samples": 9000, "duration_s": 36.0, "mean": 6.300372178819444, "std": 2.0274469583523675, "rms": 6.618552013890526, "min": 1.3330078125, "max": 7.91015625, "peak_abs": 7.91015625, "crest_factor": 1.19514906484058, "kurtosis": 4.37157052906 …

*Claude:* I think the decision was justified, though only just. If anything it came one cut late, not early. But the model's logged state points at the wrong cause: the spindle-vibration spike it flags is weak evidence, and the real sign of wear is the steadily rising spindle-motor load.

## The decision

The first `replace_insert` for insert c10 was **case 10 run 5**: probability **0.60**, exactly at the 0.60 threshold, model jev-1.14.0, state sha256 `7aaf59b1…`. Runs 1–4 were `continue` (0.42, 0.49, 0.31, 0.44). Runs 6–10 were all `replace_insert`, with probability climbing from 0.65 to 0.69.

The decision covers `public.milling`, `c10r005-{as,at,vs,vt,ac,dc}`, samples 0..9000 (36 s at 250 Hz). The setting is cast iron, depth of cut 1.5 mm, feed 0.25 mm/rev.

## What the model saw

The logged state compared run 5 with only **3 earlier sharp cuts of the same setting**: c04r001–003, from a different insert. None of insert c10's own earlier cuts were in that history. Against those 3 cuts:

| Sensor | Value that stood out | Run 5 | History median | Deviation |
|---|---|---|---|---|
| vib_spindle | kurtosis | 208 | 5.3 | **+38.6×** |
| vib_spindle | peak / crest | 0.88 V / 3.03 | 0.39 V / 1.33 | +127% |
| smcDC (spindle current, DC) | mean / rms / std | 6.76 / 7.15 / 2.33 | 5.42 / 5.66 / 1.65 | +25% / +26% / +41%, above all 3 history cuts |
| smcAC (spindle current, AC) | rms | 1.82 | 1.44 | +26%, above all 3 history cuts |
| vib_table | rms | 0.85 | 1.48 | −42%, below all 3 history cuts |
| AE spindle / AE table | — | — | — | within ±30%, nothing notable |

## What the recordings actually show

**1. The vibration spike is a short transient, not a change across the whole cut.** In `c10r005-vs` 0..9000 the signal stays flat at about 0.26–0.35 V. The exception is a burst around samples **~1140–1260** (about 4.6–5.0 s, up to 0.88 V), with smaller bursts until about sample 1700. The rms is unchanged (0.291 against 0.290 for the history median), so the kurtosis of 208 comes from about half a second of the cut.

The insert's own earlier cuts show this kind of spike is not new:
- `c10r002-vs` 0..9000 had a larger peak (0.92 V, kurtosis 92), and the model said `continue`.
- `c10r001`, `c10r003` and `c10r004` were clean (kurtosis 3.1–3.8).
- In the current 16-cut history for this setting, kurtosis 208 sits only at the 0.625 quantile.

So the 38× figure mostly reflects how few cuts the history had, not that the cut was unusual.

**2. Spindle-motor load rises steadily, and this is the real wear signal.** Mean smcDC over samples 0..9000 for each c10 cut:

| Run | 1 | 2 | 3 | 4 | **5** | 6 |
|---|---|---|---|---|---|---|
| mean (V) | 4.73 | 4.92 | 5.55 | 6.16 | **6.76** | 7.37 |
| peak (V) | 6.22 | 6.26 | 7.06 | 7.71 | **8.65** | 9.40 |

It rises every cut, about 43% from run 1 to run 5. A blunting insert needs more cutting force, which shows up as more spindle current. Run 5 is one more step on that trend, about 10% above run 4, not a sudden jump.

**3. The closest earlier cuts were worn ones.** In the current decision state:
- run 5's nearest neighbours by smcDC are `c04r006-dc`, `c04r005-dc` and `c04r007-dc`, all labelled **`not_sharp`**;
- by smcAC, `c04r005-ac` (`not_sharp`);
- by vib_table, `c04r004-vt` (`not_sharp`).

The case-4 insert was first labelled `not_sharp` at run 4, with mean smcDC **6.17** (`c04r004-dc`), up from 5.49 at its last sharp cut (`c04r003-dc`). c10 reached the same level at **run 4 (6.16)** and passed it at run 5 (6.76, above `c04r005-dc` at 6.30).

## Verdict

**Justified, and if anything one cut late.** On spindle load, run 5 matches cuts already labelled `not_sharp`, and run 4 was at the level where the case-4 insert was first labelled `not_sharp`.

Two caveats:
- **The logged state suggests the wrong reason.** Its biggest number is the vibration-kurtosis spike, which is a single transient against a 3-cut history, and a comparable spike in run 2 did not trigger a replace. The model reports a probability, not a reason, so which values actually moved it from 0.44 to 0.60 can't be read off. Still, a 0.60 call resting on that spike would be premature; one resting on the load trend is not.
- **There is no measured wear (flank-wear / VB values) for insert c10 in the collection.** The labels are only `evaluation` and `s1:*`. "Justified" here is an analogy with insert c04, which runs the same setting and has wear labels. It is not ground truth for c10.

## Recordings cited (all `public.milling`, samples 0..9000)
- **Decision cut:** `c10r005-vs`, `-dc`, `-ac`, `-vt`, `-as`, `-at`
- **Earlier c10 cuts:** `c10r001`–`c10r004` (`-vs`, `-dc`)
- **Next cut:** `c10r006-vs`, `c10r006-dc`
- **Sharp history the model saw:** `c04r001`–`c04r003`
- **Worn comparison cuts:** `c04r004-dc`, `c04r004-vt`, `c04r005-dc`, `c04r005-ac`, `c04r006-dc`, `c04r007-dc`

The 3-cut history is worth fixing: it inflates any one-off transient. Including the insert's own earlier cuts, or requiring a minimum history size, would give more stable decision states.

## Answer

I think the decision was justified, though only just. If anything it came one cut late, not early. But the model's logged state points at the wrong cause: the spindle-vibration spike it flags is weak evidence, and the real sign of wear is the steadily rising spindle-motor load.

## The decision

The first `replace_insert` for insert c10 was **case 10 run 5**: probability **0.60**, exactly at the 0.60 threshold, model jev-1.14.0, state sha256 `7aaf59b1…`. Runs 1–4 were `continue` (0.42, 0.49, 0.31, 0.44). Runs 6–10 were all `replace_insert`, with probability climbing from 0.65 to 0.69.

The decision covers `public.milling`, `c10r005-{as,at,vs,vt,ac,dc}`, samples 0..9000 (36 s at 250 Hz). The setting is cast iron, depth of cut 1.5 mm, feed 0.25 mm/rev.

## What the model saw

The logged state compared run 5 with only **3 earlier sharp cuts of the same setting**: c04r001–003, from a different insert. None of insert c10's own earlier cuts were in that history. Against those 3 cuts:

| Sensor | Value that stood out | Run 5 | History median | Deviation |
|---|---|---|---|---|
| vib_spindle | kurtosis | 208 | 5.3 | **+38.6×** |
| vib_spindle | peak / crest | 0.88 V / 3.03 | 0.39 V / 1.33 | +127% |
| smcDC (spindle current, DC) | mean / rms / std | 6.76 / 7.15 / 2.33 | 5.42 / 5.66 / 1.65 | +25% / +26% / +41%, above all 3 history cuts |
| smcAC (spindle current, AC) | rms | 1.82 | 1.44 | +26%, above all 3 history cuts |
| vib_table | rms | 0.85 | 1.48 | −42%, below all 3 history cuts |
| AE spindle / AE table | — | — | — | within ±30%, nothing notable |

## What the recordings actually show

**1. The vibration spike is a short transient, not a change across the whole cut.** In `c10r005-vs` 0..9000 the signal stays flat at about 0.26–0.35 V. The exception is a burst around samples **~1140–1260** (about 4.6–5.0 s, up to 0.88 V), with smaller bursts until about sample 1700. The rms is unchanged (0.291 against 0.290 for the history median), so the kurtosis of 208 comes from about half a second of the cut.

The insert's own earlier cuts show this kind of spike is not new:
- `c10r002-vs` 0..9000 had a larger peak (0.92 V, kurtosis 92), and the model said `continue`.
- `c10r001`, `c10r003` and `c10r004` were clean (kurtosis 3.1–3.8).
- In the current 16-cut history for this setting, kurtosis 208 sits only at the 0.625 quantile.

So the 38× figure mostly reflects how few cuts the history had, not that the cut was unusual.

**2. Spindle-motor load rises steadily, and this is the real wear signal.** Mean smcDC over samples 0..9000 for each c10 cut:

| Run | 1 | 2 | 3 | 4 | **5** | 6 |
|---|---|---|---|---|---|---|
| mean (V) | 4.73 | 4.92 | 5.55 | 6.16 | **6.76** | 7.37 |
| peak (V) | 6.22 | 6.26 | 7.06 | 7.71 | **8.65** | 9.40 |

It rises every cut, about 43% from run 1 to run 5. A blunting insert needs more cutting force, which shows up as more spindle current. Run 5 is one more step on that trend, about 10% above run 4, not a sudden jump.

**3. The closest earlier cuts were worn ones.** In the current decision state:
- run 5's nearest neighbours by smcDC are `c04r006-dc`, `c04r005-dc` and `c04r007-dc`, all labelled **`not_sharp`**;
- by smcAC, `c04r005-ac` (`not_sharp`);
- by vib_table, `c04r004-vt` (`not_sharp`).

The case-4 insert was first labelled `not_sharp` at run 4, with mean smcDC **6.17** (`c04r004-dc`), up from 5.49 at its last sharp cut (`c04r003-dc`). c10 reached the same level at **run 4 (6.16)** and passed it at run 5 (6.76, above `c04r005-dc` at 6.30).

## Verdict

**Justified, and if anything one cut late.** On spindle load, run 5 matches cuts already labelled `not_sharp`, and run 4 was at the level where the case-4 insert was first labelled `not_sharp`.

Two caveats:
- **The logged state suggests the wrong reason.** Its biggest number is the vibration-kurtosis spike, which is a single transient against a 3-cut history, and a comparable spike in run 2 did not trigger a replace. The model reports a probability, not a reason, so which values actually moved it from 0.44 to 0.60 can't be read off. Still, a 0.60 call resting on that spike would be premature; one resting on the load trend is not.
- **There is no measured wear (flank-wear / VB values) for insert c10 in the collection.** The labels are only `evaluation` and `s1:*`. "Justified" here is an analogy with insert c04, which runs the same setting and has wear labels. It is not ground truth for c10.

## Recordings cited (all `public.milling`, samples 0..9000)
- **Decision cut:** `c10r005-vs`, `-dc`, `-ac`, `-vt`, `-as`, `-at`
- **Earlier c10 cuts:** `c10r001`–`c10r004` (`-vs`, `-dc`)
- **Next cut:** `c10r006-vs`, `c10r006-dc`
- **Sharp history the model saw:** `c04r001`–`c04r003`
- **Worn comparison cuts:** `c04r004-dc`, `c04r004-vt`, `c04r005-dc`, `c04r005-ac`, `c04r006-dc`, `c04r007-dc`

The 3-cut history is worth fixing: it inflates any one-off transient. Including the insert's own earlier cuts, or requiring a minimum history size, would give more stable decision states.
