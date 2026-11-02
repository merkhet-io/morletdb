# A real Claude Code session on the machine demo: a fault signature across tests

Recorded on 2026-11-02 with Claude Code in headless mode (`claude -p`), model `claude-opus-5-5`, in
`examples/machine-demo` with only the `morletdb` MCP server allowed (no shell, file or web
tools). One unattended run, not edited: 40 turns, 113 s, USD 1.01. Tool results are
shortened here; Claude's text is verbatim.

The database holds IMS tests 2 and 3 (test 3 one snapshot an hour). One run, the first with this prompt; it is replayed on the website.

## Prompt

> Bearing 1 in test 2 failed with an outer-race fault. Characterise its fault signature, then find out whether the same signature appears on any bearing in test 3, when it first appears there, and whether you can confirm that it is the same kind of fault. Cite the snapshots you base this on.

## Tool calls

1. `morletdb.list_collections()`  
   → public.bearings: 8152 recordings, 166952960 samples

2. `morletdb.list_recordings(collection="public.bearings", limit=3, include_metadata=true)`  
   → 8152 recordings; first: B1.20040212T103239, B1.20040212T104239, B1.20040212T105239

3. `morletdb.coverage(collection="public.bearings", group_by="test")`  
   → test 2: 3936 recordings, continuous, every 0 s, 983 gaps; test 3: 4216 recordings, continuous, every 0 s, 1053 gaps

4. `morletdb.list_recordings(collection="public.bearings", metadata_filter="{\"test\": \"3\"}", limit=2, include_metadata=true)`  
   → 4216 recordings; first: B1.20040304T092746, B1.20040304T102246

5. `morletdb.trend(collection="public.bearings", metadata_filter="{\"test\": \"2\", \"bearing\": \"1\"}", max_points=120)`  
   → 120 points; first sustained change: rms 2004-02-16T17:52, kurtosis 2004-02-17T07:32, crest_factor none, 0-625 Hz 2004-02-18T23:32, 625-1250 Hz 2004-02-18T09:42, 1250-2500 Hz 2004-02-17T07:32, 2500-5000 Hz 2004-02-16T09:32, 5000-10000 Hz 2004-02-17T07:32

*Claude:* Test 2 bearing 1's 2.5–5 kHz band starts rising around 2004-02-16 04:02 and jumps on 02-17 07:32. Next I'm checking its envelope spectrum for the computed defect frequencies.

6. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B1.20040217T073239", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 7.7 dB; strongest: 230.47 Hz 36.1 dB, 460.94 Hz 30.9 dB, 123.05 Hz 13.4 dB; checks: 236.4 Hz: 230.47 Hz 36.1 dB, 460.94 Hz 30.9 dB; 296.9 Hz: 288.09 Hz 8.5 dB, 585.94 Hz 4.7 dB; 139.9 Hz: 143.55 Hz 2.6 dB, 288.09 Hz 8.5 dB, 407.23 Hz 4.1 dB; 14.8 Hz: 14.65 Hz 6.4 dB, 28.32 Hz 7.7 dB, 45.9 Hz 6.4 dB; 33.3 Hz: 33.2 Hz 6.3 dB, 65.43 Hz 9.2 dB, 102.54 Hz 3.4 dB

7. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B1.20040216T040239", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 7.1 dB; strongest: 230.47 Hz 18.4 dB, 28.32 Hz 13.4 dB, 460.94 Hz 12.8 dB; checks: 236.4 Hz: 230.47 Hz 18.4 dB, 460.94 Hz 12.8 dB; 296.9 Hz: 290.04 Hz 5.5 dB, 582.03 Hz 6.2 dB; 139.9 Hz: 141.6 Hz 7.8 dB, 286.13 Hz 4.8 dB, 414.06 Hz 5.9 dB; 14.8 Hz: 14.65 Hz 11.4 dB, 28.32 Hz 13.4 dB, 42.97 Hz 4.7 dB; 33.3 Hz: 33.2 Hz 9.0 dB, 68.36 Hz 1.5 dB, 102.54 Hz 3.5 dB

8. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B1.20040212T103239", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 7.3 dB; strongest: 32.23 Hz 13.9 dB, 14.65 Hz 12.9 dB, 29.3 Hz 12.5 dB; checks: 236.4 Hz: 232.42 Hz 6.9 dB, 463.87 Hz 7.4 dB; 296.9 Hz: 293.95 Hz 4.2 dB, 587.89 Hz 2.2 dB; 139.9 Hz: 135.74 Hz 5.8 dB, 285.16 Hz 7.6 dB, 409.18 Hz 4.7 dB; 14.8 Hz: 14.65 Hz 12.9 dB, 29.3 Hz 12.5 dB, 44.92 Hz 7.9 dB; 33.3 Hz: 32.23 Hz 13.9 dB, 65.43 Hz 5.1 dB, 100.59 Hz 10.8 dB

*Claude:* Test 2 bearing 1's signature is a 230.5 Hz envelope line plus its 2× harmonic, within 2.5% of the computed outer-race frequency. Next I'm surveying the four test 3 bearings.

9. `morletdb.trend(collection="public.bearings", metadata_filter="{\"test\": \"3\", \"bearing\": \"1\"}", max_points=60, bands="2500-5000")`  
   → 60 points; first sustained change: rms none, kurtosis none, crest_factor none, 2500-5000 Hz none

10. `morletdb.trend(collection="public.bearings", metadata_filter="{\"test\": \"3\", \"bearing\": \"2\"}", max_points=60, bands="2500-5000")`  
   → 60 points; first sustained change: rms none, kurtosis none, crest_factor none, 2500-5000 Hz none

11. `morletdb.trend(collection="public.bearings", metadata_filter="{\"test\": \"3\", \"bearing\": \"3\"}", max_points=60, bands="2500-5000")`  
   → 60 points; first sustained change: rms 2004-03-25T16:51, kurtosis none, crest_factor none, 2500-5000 Hz 2004-04-15T19:52

12. `morletdb.trend(collection="public.bearings", metadata_filter="{\"test\": \"3\", \"bearing\": \"4\"}", max_points=60, bands="2500-5000")`  
   → 60 points; first sustained change: rms 2004-04-16T13:52, kurtosis none, crest_factor none, 2500-5000 Hz none

13. `morletdb.search_from_time(collection="public.bearings", utc_time="2004-02-17T07:32:39.5Z", metadata_filter="{\"test\": \"2\", \"bearing\": \"1\"}", search_filter="{\"test\": \"3\"}", top_k=15)`  
   → 15 hits: B3.20040417T055255 2004-04-17T05:52:55 d=0.2022; B3.20040417T015255 2004-04-17T01:52:55 d=0.2397; B4.20040417T025255 2004-04-17T02:52:55 d=0.2402; B4.20040417T015255 2004-04-17T01:52:55 d=0.2497; B3.20040417T045255 2004-04-17T04:52:55 d=0.2597; B4.20040417T005255 2004-04-17T00:52:55 d=0.2706; B3.20040417T065255 2004-04-17T06:52:55 d=0.2708; B3.20040416T235255 2004-04-16T23:52:55 d=0.2772; B3.20040417T165255 2004-04-17T16:52:55 d=0.2807; B3.20040417T025255 2004-04-17T02:52:55 d=0.284; B3.20040417T135255 2004-04-17T13:52:55 d=0.2899; B3.20040417T155255 2004-04-17T15:52:55 d=0.2956; B3.20040417T125255 2004-04-17T12:52:55 d=0.2986; B3.20040417T005255 2004-04-17T00:52:55 d=0.3007; B3.20040417T175255 2004-04-17T17:52:55 d=0.3011

*Claude:* Similarity search from the test 2 fault snapshot mostly returns test 3 bearing 3, plus some bearing 4, all from 16–17 April. Next I'm checking those hits' envelope spectra for the 230 Hz line.

14. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040417T055255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 8.1 dB; strongest: 231.45 Hz 35.8 dB, 462.89 Hz 27.8 dB, 42.97 Hz 13.6 dB; checks: 236.4 Hz: 231.45 Hz 35.8 dB, 462.89 Hz 27.8 dB; 296.9 Hz: 294.92 Hz 11.9 dB, 591.8 Hz 4.0 dB; 139.9 Hz: 142.58 Hz 7.9 dB, 274.41 Hz 10.2 dB, 415.04 Hz 4.0 dB; 14.8 Hz: 14.65 Hz 2.1 dB, 29.3 Hz 3.4 dB, 42.97 Hz 13.6 dB; 33.3 Hz: 32.23 Hz 10.4 dB, 65.43 Hz 10.7 dB, 98.63 Hz 7.1 dB

15. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040417T025255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 7.2 dB; strongest: 231.45 Hz 32.5 dB, 462.89 Hz 18.5 dB, 259.77 Hz 10.3 dB; checks: 236.4 Hz: 231.45 Hz 32.5 dB, 462.89 Hz 18.5 dB; 296.9 Hz: 289.06 Hz 5.8 dB, 589.84 Hz 6.8 dB; 139.9 Hz: 141.6 Hz 6.9 dB, 275.39 Hz 6.6 dB, 430.66 Hz 7.5 dB; 14.8 Hz: 16.6 Hz 4.6 dB, 31.25 Hz 4.5 dB, 43.95 Hz 3.6 dB; 33.3 Hz: 32.23 Hz 8.6 dB, 68.36 Hz 6.8 dB, 98.63 Hz 8.3 dB

16. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B1.20040417T075255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 7.0 dB; strongest: 57.62 Hz 13.7 dB, 33.2 Hz 12.8 dB, 61.52 Hz 10.1 dB; checks: 236.4 Hz: 243.16 Hz 7.6 dB, 475.59 Hz 6.9 dB; 296.9 Hz: 289.06 Hz 6.1 dB, 578.12 Hz 5.7 dB; 139.9 Hz: 140.62 Hz 5.2 dB, 271.48 Hz 4.6 dB, 420.9 Hz 8.8 dB; 14.8 Hz: 16.6 Hz 8.0 dB, 29.3 Hz 8.1 dB, 44.92 Hz -0.4 dB; 33.3 Hz: 33.2 Hz 12.8 dB, 67.38 Hz 6.2 dB, 100.59 Hz 8.2 dB

17. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B2.20040417T075255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9,139.9,14.8,33.3")`  
   → noise peaks 8.1 dB; strongest: 33.2 Hz 16.5 dB, 231.45 Hz 12.7 dB, 57.62 Hz 12.4 dB; checks: 236.4 Hz: 231.45 Hz 12.7 dB, 478.52 Hz 7.0 dB; 296.9 Hz: 302.73 Hz 8.2 dB, 587.89 Hz 6.6 dB; 139.9 Hz: 136.72 Hz 4.7 dB, 275.39 Hz 2.5 dB, 429.69 Hz 4.5 dB; 14.8 Hz: 14.65 Hz 6.5 dB, 28.32 Hz 7.4 dB, 44.92 Hz 3.8 dB; 33.3 Hz: 33.2 Hz 16.5 dB, 65.43 Hz 11.5 dB, 98.63 Hz 10.4 dB

*Claude:* Bearings 3 and 4 in test 3 both carry the 231.45 Hz line plus harmonic; bearings 1 and 2 don't show a clear one. Next I'm tracing back in time to find when the line first appears on each.

18. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T135255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 7.3 dB; strongest: 231.45 Hz 21.6 dB, 462.89 Hz 20.3 dB, 261.72 Hz 11.5 dB; checks: 236.4 Hz: 231.45 Hz 21.6 dB, 462.89 Hz 20.3 dB; 296.9 Hz: 294.92 Hz 9.7 dB, 594.73 Hz 6.6 dB

19. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040415T195255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 7.0 dB; strongest: 261.72 Hz 12.3 dB, 33.2 Hz 11.9 dB, 294.92 Hz 11.3 dB; checks: 236.4 Hz: 229.49 Hz 7.5 dB, 468.75 Hz 1.7 dB; 296.9 Hz: 294.92 Hz 11.3 dB, 589.84 Hz 7.7 dB

20. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040412T135157", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 7.0 dB; strongest: 29.3 Hz 10.6 dB, 73.24 Hz 9.8 dB, 290.04 Hz 9.5 dB; checks: 236.4 Hz: 236.33 Hz 5.2 dB, 477.54 Hz 3.4 dB; 296.9 Hz: 290.04 Hz 9.5 dB, 578.12 Hz 6.8 dB

21. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040405T035157", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 6.5 dB; strongest: 177.73 Hz 10.5 dB, 356.45 Hz 9.3 dB, 248.05 Hz 8.9 dB; checks: 236.4 Hz: 242.19 Hz 5.4 dB, 470.7 Hz 6.6 dB; 296.9 Hz: 298.83 Hz 6.7 dB, 580.08 Hz 5.1 dB

22. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040325T165157", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 7.1 dB; strongest: 42.97 Hz 11.4 dB, 14.65 Hz 10.4 dB, 34.18 Hz 10.1 dB; checks: 236.4 Hz: 230.47 Hz 8.7 dB, 465.82 Hz 8.4 dB; 296.9 Hz: 305.66 Hz 5.9 dB, 599.61 Hz 6.7 dB

23. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040304T092746", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4,296.9", top_peaks=4)`  
   → noise peaks 7.4 dB; strongest: 33.2 Hz 14.0 dB, 21.48 Hz 12.2 dB, 25.39 Hz 9.5 dB; checks: 236.4 Hz: 238.28 Hz 6.5 dB, 473.63 Hz 4.9 dB; 296.9 Hz: 288.09 Hz 4.7 dB, 594.73 Hz 1.8 dB

*Claude:* On bearing 3 the line is absent at 04-15 19:52 and clear at 04-16 13:52. Next I'm narrowing that window and checking bearing 4 over the same times.

24. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T015255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.1 dB; strongest: 261.72 Hz 10.4 dB, 7.81 Hz 10.4 dB, 33.2 Hz 10.3 dB; checks: 236.4 Hz: 229.49 Hz 4.2 dB, 486.33 Hz 6.8 dB

25. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T075255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 8.2 dB; strongest: 33.2 Hz 13.2 dB, 261.72 Hz 13.1 dB, 130.86 Hz 11.3 dB; checks: 236.4 Hz: 229.49 Hz 6.5 dB, 471.68 Hz 5.2 dB

26. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T075255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.8 dB; strongest: 261.72 Hz 13.0 dB, 229.49 Hz 13.0 dB, 98.63 Hz 11.6 dB; checks: 236.4 Hz: 229.49 Hz 13.0 dB, 471.68 Hz 6.9 dB

27. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T135255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.7 dB; strongest: 231.45 Hz 22.6 dB, 462.89 Hz 18.4 dB, 33.2 Hz 11.2 dB; checks: 236.4 Hz: 231.45 Hz 22.6 dB, 462.89 Hz 18.4 dB

28. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040415T195255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.2 dB; strongest: 33.2 Hz 16.2 dB, 98.63 Hz 10.4 dB, 29.3 Hz 9.3 dB; checks: 236.4 Hz: 229.49 Hz 6.6 dB, 467.77 Hz 8.2 dB

*Claude:* Bearing 4 shows a weak 229.5 Hz line (5 dB above noise) at 04-16 07:52, before bearing 3 does. That makes attributing the fault harder, so I'm scanning both bearings hour by hour.

29. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T095255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 6.9 dB; strongest: 231.45 Hz 21.4 dB, 462.89 Hz 20.5 dB, 32.23 Hz 10.1 dB; checks: 236.4 Hz: 231.45 Hz 21.4 dB, 462.89 Hz 20.5 dB

30. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T115255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.4 dB; strongest: 231.45 Hz 22.3 dB, 462.89 Hz 21.4 dB, 63.48 Hz 12.2 dB; checks: 236.4 Hz: 231.45 Hz 22.3 dB, 462.89 Hz 21.4 dB

31. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T095255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.2 dB; strongest: 231.45 Hz 21.3 dB, 462.89 Hz 15.8 dB, 79.1 Hz 10.3 dB; checks: 236.4 Hz: 231.45 Hz 21.3 dB, 462.89 Hz 15.8 dB

32. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T115255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 6.3 dB; strongest: 231.45 Hz 23.0 dB, 462.89 Hz 19.9 dB, 33.2 Hz 13.6 dB; checks: 236.4 Hz: 231.45 Hz 23.0 dB, 462.89 Hz 19.9 dB

33. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T045255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.3 dB; strongest: 33.2 Hz 11.2 dB, 261.72 Hz 11.0 dB, 42.97 Hz 10.7 dB; checks: 236.4 Hz: 243.16 Hz 10.3 dB, 481.45 Hz 5.3 dB

34. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T045255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.5 dB; strongest: 33.2 Hz 11.5 dB, 393.55 Hz 10.8 dB, 66.41 Hz 10.3 dB; checks: 236.4 Hz: 234.38 Hz 8.1 dB, 482.42 Hz 6.2 dB

35. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B3.20040416T085255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 7.0 dB; strongest: 231.45 Hz 22.0 dB, 462.89 Hz 19.6 dB, 261.72 Hz 11.9 dB; checks: 236.4 Hz: 231.45 Hz 22.0 dB, 462.89 Hz 19.6 dB

36. `morletdb.envelope_spectrum(collection="public.bearings", recording_id="B4.20040416T085255", start_sample=0, end_sample=20480, band="2500-5000", check_frequencies="236.4", top_peaks=3)`  
   → noise peaks 8.0 dB; strongest: 231.45 Hz 20.3 dB, 462.89 Hz 16.0 dB, 33.2 Hz 12.5 dB; checks: 236.4 Hz: 231.45 Hz 20.3 dB, 462.89 Hz 16.0 dB

37. `morletdb.compare_intervals(collection="public.bearings", recording_id="B3.20040417T055255", start_sample=0, end_sample=20480, reference_recording_id="B3.20040304T092746", reference_start_sample=0, reference_end_sample=20480, bands="0-1250,1250-2500,2500-5000,5000-10000")`  
   → 9.9 dB above reference; excess by band: 0-1250 Hz 0.14, 1250-2500 Hz 0.03, 2500-5000 Hz 0.69, 5000-10000 Hz 0.15

38. `morletdb.compare_intervals(collection="public.bearings", recording_id="B4.20040417T055255", start_sample=0, end_sample=20480, reference_recording_id="B4.20040304T092746", reference_start_sample=0, reference_end_sample=20480, bands="0-1250,1250-2500,2500-5000,5000-10000")`  
   → 4.21 dB above reference; excess by band: 0-1250 Hz 0.14, 1250-2500 Hz 0.21, 2500-5000 Hz 0.62, 5000-10000 Hz 0.02

## Answer

# Test 2 bearing 1's outer-race signature, and where it shows up in test 3

**Short answer:** Yes, the same signature appears in test 3, first on bearings 3 and 4 together at 2004-04-16 08:52:55, about 41 hours before the test ends. Bearing 3 is the more likely source. The data fits the same kind of fault (outer race), but it can't prove it: that would take a teardown.

## 1. Test 2 bearing 1: the signature

The shaft turns at 2000 rpm (33.3 Hz). From the ZA-2115 bearing geometry, the outer-race defect frequency is 236.4 Hz. The inner-race (296.9 Hz), rolling-element (139.9 Hz) and cage (14.8 Hz) frequencies serve as checks.

- **What the trend shows:** the 2.5–5 kHz band starts rising around 02-16 04:02 and jumps on 02-17 07:32. At that jump the RMS goes from 0.107 to 0.171 and kurtosis from 4.1 to 5.2. This band is where the impacts excite the housing.
- **What the envelope spectrum shows** (filtered to 2.5–5 kHz):
  - a line at **230.5 Hz**, 2.5% below the computed outer-race frequency (a few percent off is normal because of slip);
  - a strong **2× harmonic at 460.9 Hz**;
  - no lines at the inner-race, rolling-element or cage frequencies.

| Snapshot (public.bearings, samples 0..20480) | 230.5 Hz line | 2× (460.9 Hz) |
|---|---|---|
| `B1.20040212T103239` (start of test) | absent (−0.4 dB vs noise) | absent |
| `B1.20040216T040239` (onset) | +11.3 dB | +5.6 dB |
| `B1.20040217T073239` (fully developed) | +28.4 dB | +23.2 dB |

## 2. Test 3: where it appears

- **Search:** I searched test 3 using the developed test 2 snapshot (`B1.20040217T073239`, samples 8976..13072). All 15 top hits were bearing 3 or 4 on 04-16/17, at distances 0.20–0.30 (strong matches). The best was `B3.20040417T055255` 0..4096, at distance 0.20.
- **Envelope spectra:** these confirm it. `B3.20040417T055255` has 231.45 Hz at +27.7 dB with its 2× at +19.6 dB. `B4.20040417T025255` has 231.45 Hz at +25.3 dB. That is the same frequency as in test 2, within one frequency bin (0.98 Hz), with the same harmonic and the same 2.5–5 kHz band.
- **Bearings 1 and 2 don't carry it:**
  - `B1.20040417T075255`: nothing at 231 Hz.
  - `B2.20040417T075255`: only a weak +4.6 dB at 231 Hz with no harmonic, most likely picked up from the neighbouring bearings.

## 3. When it first appears

Bearing 3, envelope line at about 231 Hz:

| Snapshot | Result |
|---|---|
| `B3.20040304T092746`, `…0325T165157`, `…0405T035157`, `…0412T135157`, `…0415T195255`, `…0416T015255`, `…0416T045255` | absent (≤ +1.7 dB) |
| `B3.20040416T075255` | absent (−1.7 dB) |
| **`B3.20040416T085255`** | **+14.9 dB, 2× at +12.5 dB** (first clear appearance) |
| `B3.20040416T095255`, `…115255`, `…135255` | +14 to +15 dB, holding steady |

Bearing 4 over the same times:

- `B4.20040415T195255` and `B4.20040416T045255`: absent.
- `B4.20040416T075255`: a weak +5.2 dB line with no harmonic. This is borderline.
- `B4.20040416T085255`: +12.3 dB with its 2× at +8.0 dB, so clear in the same hour as bearing 3.

So the signature appears **between 07:52 and 08:52 on 2004-04-16**. I checked hourly snapshots only around that point; earlier I sampled roughly one snapshot every few days, so a weak earlier precursor can't be ruled out.

## 4. Which bearing, and whether it's the same kind of fault

**Bearing 3 is the more likely source.** I compared each bearing's 04-17 05:52 snapshot with its own 03-04 baseline:

| | Bearing 3 | Bearing 4 |
|---|---|---|
| Rise in the 2.5–5 kHz band | +13.7 dB | +4.6 dB |
| Excess power in that band | 0.0264 | 0.0031 (about 8.5× less than bearing 3) |
| Kurtosis | 3.6 → 4.6 | 3.6 → 3.2 |

The trend points the same way: bearing 3's band power is already up at 04-15 19:52 (0.0018 against a baseline of 0.00095). Bearing 4's weak line an hour earlier slightly weakens this attribution.

**Same kind of fault:** consistent, but not confirmed. Both tests show the same repetition rate within a bin, the same harmonic pattern, the same resonance band, and no inner-race, rolling-element or cage lines. That is what an outer-race defect looks like, but an envelope line only shows periodic impacts at that rate. The dataset's own documentation reports an outer-race failure on bearing 3 at the end of test 3. That label isn't in the stored metadata, and I didn't use it in this analysis.

**Unexplained:** a 261.7 Hz line (about 7.85× shaft speed) shows up on bearings 3 and 4 around 04-15 and 04-16, before the onset. It matches none of the defect frequencies, and I haven't explained it.
