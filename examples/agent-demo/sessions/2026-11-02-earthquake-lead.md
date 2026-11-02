# A real Claude Code session on the agent demo

Recorded on 2026-11-02 with Claude Code in headless mode (`claude -p`), model `claude-opus-5-5`, in
`examples/agent-demo` with only the two demo MCP servers allowed (`morletdb`, `seismo`; no shell, file or web
tools). One unattended run, not edited: 30 turns, 129 s, USD 0.98. Tool results are
shortened here; Claude's text is verbatim.

## Prompt

> There was a magnitude 2.6 earthquake offshore Tarapacá on 14 March 2025 at about 21:03 UTC. Find it at station PB01, describe the signal, and find where similar signals occur in the archive. Check the matches against the earthquake catalogue. If a strong match is not in the catalogue, find out whether it is a real earthquake.

## Tool calls

1. `morletdb.list_collections()`  
   → public.seismic: 77 recordings, 135617113 samples

2. `seismo.list_stations()`  
   → PB01, PB02, PB06, PSGCX

3. `seismo.catalog_events(station="PB01", start_utc="2025-03-14T20:50:00Z", end_utc="2025-03-14T21:20:00Z")`  
   → 1 events: 2025-03-14T21:02:50 M2.6 Offshore Tarapaca, Chile, P at PB01 21:03:10

4. `morletdb.locate_time(collection="public.seismic", utc_time="2025-03-14T21:03:10Z", metadata_filter="{\"station\": \"PB01\"}")`  
   → CX.PB01..HHZ.20250312T010454 sample 24469588

5. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24463588, end_sample=24478588, min_frequency_hz=2)`  
   → onsets: 21:03:07.27 (ratio 5.54), 21:03:09.11 (ratio 1818.58), 21:03:30.48 (ratio 9.94)

6. `morletdb.compare_intervals(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24469400, end_sample=24473400, bands="0.1-1,1-2,2-5,5-10,10-20,20-50")`  
   → 9.41 dB above median of 30 windows; excess by band: 0.1-1 Hz 0.00, 1-2 Hz 0.07, 2-5 Hz 0.37, 5-10 Hz 0.49, 10-20 Hz 0.07, 20-50 Hz 0.00

7. `morletdb.describe_interval(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24469400, end_sample=24473400, min_frequency_hz=1, bands="0.1-1,1-2,2-5,5-10,10-20,20-50")`  
   → peak 2797.0, crest 3.3, dominant 5.9 Hz, centroid 5.7 Hz

8. `morletdb.read_interval(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24468400, end_sample=24476400, max_points=160)`  
   → 80 min/max buckets of 100 samples from 2025-03-14T21:02:58; peak 2797.0

*Claude:* The quake is clear at PB01: P arrives at about 21:03:09–10 UTC, close to the predicted time. Next I'll search the archive for similar signals.

9. `morletdb.search_similar(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24468000, end_sample=24472096, top_k=15)`  
   → 15 hits: CX.PB01..HHZ.20250310T235954 2025-03-11T00:38:08 d=0.1215; CX.PB02..HHZ.20250312T021305 2025-03-13T18:49:11 d=0.14; CX.PB02..HHZ.20250310T235959 2025-03-11T15:08:58 d=0.1436; CX.PB02..HHZ.20250310T235959 2025-03-11T14:32:06 d=0.1607; CX.PB02..HHZ.20250312T021305 2025-03-13T07:16:37 d=0.1632; CX.PB06..HHZ.20250312T015937 2025-03-13T08:09:42 d=0.1634; CX.PB01..HHZ.20250310T235954 2025-03-11T10:31:22 d=0.1637; CX.PB06..HHZ.20250312T015937 2025-03-14T17:45:37 d=0.1645; CX.PB02..HHZ.20250312T021305 2025-03-12T08:16:57 d=0.1663; CX.PB02..HHZ.20250312T021305 2025-03-12T02:40:03 d=0.1666; CX.PB02..HHZ.20250312T021305 2025-03-12T11:01:48 d=0.1667; CX.PB06..HHZ.20250312T015937 2025-03-12T03:33:49 d=0.1728; CX.PB06..HHZ.20250312T015937 2025-03-14T14:26:58 d=0.173; CX.PB01..HHZ.20250312T010454 2025-03-13T18:45:10 d=0.1734; CX.PB02..HHZ.20250312T021305 2025-03-14T04:34:13 d=0.1757

10. `seismo.catalog_events(station="PB01")`  
   → 52 events

11. `seismo.catalog_events(station="PB02")`  
   → 52 events

12. `seismo.catalog_events(station="PB06")`  
   → 52 events

13. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB02..HHZ.20250312T021305", start_sample=14612576, end_sample=14622672, min_frequency_hz=2)`  
   → onsets: 18:49:26.01 (ratio 22.97), 18:49:47.81 (ratio 4.31)

14. `morletdb.locate_time(collection="public.seismic", utc_time="2025-03-13T18:48:30Z")`  
   → CX.PB01..HHZ.20250312T010454 sample 15021588; CX.PB02..HHZ.20250312T021305 sample 14612465; CX.PB06..HHZ.20250312T015937 sample 14693279; CX.PSGCX..HHZ.20250312T005629 sample 15072082

*Claude:* PB02 shows P at about 18:49:26 and a likely S about 22 s later, which puts the source roughly 170 km away. Now I'll check whether the other three stations recorded it.

15. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=15021588, end_sample=15036588, min_frequency_hz=2)`  
   → onsets: 18:49:28.47 (ratio 35.58)

16. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB06..HHZ.20250312T015937", start_sample=14693279, end_sample=14708279, min_frequency_hz=2)`  
   → onsets: 18:49:00.98 (ratio 19.79)

17. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PSGCX..HHZ.20250312T005629", start_sample=15072082, end_sample=15087082, min_frequency_hz=2)`  
   → onsets: 18:49:53.65 (ratio 7.56), 18:50:40.69 (ratio 4.86)

18. `morletdb.read_interval(collection="public.seismic", recording_id="CX.PB06..HHZ.20250312T015937", start_sample=14696000, end_sample=14700000, max_points=160)`  
   → 80 min/max buckets of 50 samples from 2025-03-13T18:48:57; peak 3767.0

*Claude:* PB06's P arrives at about 18:48:59, with stronger phases about 5.5 s and 12 s after it. I'll fit the arrival times at all four stations to a trial location.

19. `seismo.predict_arrivals(origin_utc="2025-03-13T18:48:45Z", latitude=-23.4, longitude=-69.6, depth_km=100)`  
   → PB01 P 18:49:22 S 18:49:51; PB02 P 18:49:18 S 18:49:44; PB06 P 18:49:02 S 18:49:15; PSGCX P 18:49:41 S 18:50:26

20. `seismo.predict_arrivals(origin_utc="2025-03-13T18:48:45Z", latitude=-23.2, longitude=-70.4, depth_km=40)`  
   → PB01 P 18:49:20 S 18:49:48; PB02 P 18:49:15 S 18:49:38; PB06 P 18:49:01 S 18:49:13; PSGCX P 18:49:38 S 18:50:20

21. `seismo.predict_arrivals(origin_utc="2025-03-13T18:48:45Z", latitude=-23.2, longitude=-69.55, depth_km=10)`  
   → PB01 P 18:49:20 S 18:49:48; PB02 P 18:49:17 S 18:49:42; PB06 P 18:48:54 S 18:49:01; PSGCX P 18:49:41 S 18:50:24

22. `seismo.predict_arrivals(origin_utc="2025-03-13T18:48:45Z", latitude=-23, longitude=-69.5, depth_km=10)`  
   → PB01 P 18:49:18 S 18:49:43; PB02 P 18:49:14 S 18:49:37; PB06 P 18:48:51 S 18:48:55; PSGCX P 18:49:38 S 18:50:20

23. `morletdb.compare_intervals(collection="public.seismic", recording_id="CX.PB02..HHZ.20250312T021305", start_sample=14617900, end_sample=14621900, bands="0.1-1,1-2,2-5,5-10,10-20,20-50")`  
   → 5.2 dB above median of 30 windows; excess by band: 0.1-1 Hz 0.00, 1-2 Hz 0.00, 2-5 Hz 0.16, 5-10 Hz 0.34, 10-20 Hz 0.41, 20-50 Hz 0.08

24. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB06..HHZ.20250312T015937", start_sample=14669279, end_sample=14693279, min_frequency_hz=2)`  
   → onsets: 18:47:34.63 (ratio 2236.68)

25. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=14997588, end_sample=15009588, min_frequency_hz=2)`  
   → onsets: none

*Claude:* PB06 also has a much stronger uncatalogued event at 18:47:34, about 90 s earlier. The PB01 18:45 hit shows no onset. I'll check whether the 18:47 event reached the other stations.

26. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB02..HHZ.20250312T021305", start_sample=14600465, end_sample=14612565, min_frequency_hz=2)`  
   → onsets: 18:47:12.31 (ratio 6.22), 18:48:00.22 (ratio 19.07), 18:48:23.10 (ratio 4.64)

27. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=15009588, end_sample=15021688, min_frequency_hz=2)`  
   → onsets: 18:47:00.98 (ratio 13.32), 18:48:02.68 (ratio 149.64)

28. `morletdb.read_interval(collection="public.seismic", recording_id="CX.PB06..HHZ.20250312T015937", start_sample=14687400, end_sample=14690400, max_points=120)`  
   → 60 min/max buckets of 50 samples from 2025-03-13T18:47:31; peak 5365.0

## Answer

I found the M2.6 at PB01, and most of its closest matches are not in the catalogue. I checked the strongest of those: it is a real seismic event, but I can't say for certain that it is a natural earthquake rather than a mine blast.

## 1. The 14 March event at PB01

`public.seismic`, `CX.PB01..HHZ.20250312T010454`, samples 24469400..24473400.

- **Arrival:** the catalogue has the origin at 21:02:50.6 UTC, 116.7 km from PB01, with P predicted at 21:03:10.8. The data show P at about 21:03:09–10 UTC. The trigger is very sharp: short-term energy rose to about 1800 times the preceding level.
- **Shape:** an impulsive P, a lull, then a larger later phase about 20 s after P, near 21:03:30. Peak amplitude is about 2800 counts. The model predicted an S-P gap of about 14.7 s, so either that later phase isn't S or the event is farther away than the catalogue says. The catalogue depth looks fixed at 10 km, so its location may be rough.
- **Frequency content:** the signal is 9.4 dB above the surrounding background overall. Almost all of the new energy is between 2 and 10 Hz (37% at 2–5 Hz, 49% at 5–10 Hz), with the strongest frequency near 6 Hz. Below 1 Hz nothing changes: that band is the ever-present ocean noise.

## 2. Similar signals in the archive

I searched with a 41 s window starting 15 s before P. All 15 matches scored 0.12–0.18, which counts as strong (below 0.3). I compared each one with the predicted arrival times of every catalogued event at that station:

| Distance | Station | Window start (UTC) | Catalogue |
|---|---|---|---|
| 0.122 | PB01 | 11 Mar 00:38:08 | ✅ M3.1 at 136 km depth, Antofagasta; P 00:38:24, S 00:38:43 |
| 0.140 | PB02 | 13 Mar 18:49:11 | ❌ nothing |
| 0.144 | PB02 | 11 Mar 15:08:58 | ❌ |
| 0.161 | PB02 | 11 Mar 14:32:06 | ❌ |
| 0.163–0.176 | PB02 / PB06 / PB01 (11 more) | 11–14 Mar, various times | ❌ none |

Only 1 of the 15 matches a catalogued event. That doesn't make the others false: this catalogue lists almost nothing below about M2.5, so small local events will be missing from it.

## 3. The strongest uncatalogued match is a real seismic event

The match is `public.seismic`, `CX.PB02..HHZ.20250312T021305`, 14616576..14620672. I checked whether it shows up at all four stations within a few minutes:

| Station | P onset (UTC, 13 Mar) | Strength of trigger |
|---|---|---|
| PB06 | about 18:48:59 (S-P about 5.5 s) | ratio 20 |
| PB02 | 18:49:26 (S at about 18:49:48, S-P about 22 s) | ratio 23 |
| PB01 | 18:49:28.5 | ratio 36 |
| PSGCX | 18:49:54 | ratio 7.6, weak |

The wave passes from south to north across the network, with sensible travel times. A local noise burst would appear at only one station; this is one wavefront from one source. I tried a few trial locations against the model times. The best of those, a shallow source near **23.0°S, 69.5°W**, about 35–50 km south of PB06, matches most of the observations:
- the PB02 and PB01 delays to within about 3 s;
- PB02's S-P (22.8 s predicted, 21.8 s observed);
- PB06's short S-P.

PSGCX arrives about 7 s late against that location, and its trigger is the weakest of the four. This is a rough fit, not a computed location.

**A second, larger event came from the same place about 90 s earlier.** PB06 shows P at 18:47:34 (trigger ratio about 2200, peak about 5400 counts, S-P about 6.5 s). The same pattern then appears at PB02 (18:48:00) and PB01 (18:48:03). It isn't in the catalogue either.

**Earthquake or blast? I can't tell from this data.**
- **Points towards blasting:**
  - The source is shallow and in the Antofagasta copper-mining district.
  - Both events happened at about 15:47–15:49 Chilean local time, in the daytime.
  - Two events came from the same spot 90 s apart.
  - The signal at PB02 is high-pitched (average frequency about 9 Hz, 41% of the new energy at 10–20 Hz), against about 5 Hz for the catalogued event at PB01. Part of that difference may come from the different paths and stations.
- **Points towards an earthquake:** PB06 records clear, large S waves, which blasts usually produce only weakly.

To settle it you would need the horizontal channels (this archive has only the vertical), first-motion directions, or the Chilean national seismic network's (CSN) list of quarry blasts. My working conclusion is a real, uncatalogued local event of about M2.5–3, possibly a mine blast.

## Not done
- I didn't investigate the other 12 uncatalogued matches.
- The PB01 match at 13 Mar 18:45:10 (distance 0.173) has no clear onset, so it is probably noise that happens to look similar.
- I didn't calculate magnitudes; the M2.5–3 figure is only a comparison of amplitudes with the catalogued event.
