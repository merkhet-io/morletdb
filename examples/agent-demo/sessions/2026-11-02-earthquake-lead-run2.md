# A real Claude Code session on the agent demo

Recorded on 2026-11-02 with Claude Code in headless mode (`claude -p`), model `claude-opus-5-5`, in
`examples/agent-demo` with only the two demo MCP servers allowed (`morletdb`, `seismo`; no shell, file or web
tools). One unattended run, not edited: 24 turns, 91 s, USD 0.94. Tool results are
shortened here; Claude's text is verbatim.

Run 2, the same prompt after adding `search_from_time`, which finds the onset and frames the window itself; 8 of its 15 hits are catalogued earthquakes. [Run 1](2026-11-02-earthquake-lead-run1.md) is the run before.

## Prompt

> There was a magnitude 2.6 earthquake offshore Tarapacá on 14 March 2025 at about 21:03 UTC. Find it at station PB01, describe the signal, and find where similar signals occur in the archive. Check the matches against the earthquake catalogue. If a strong match is not in the catalogue, find out whether it is a real earthquake.

## Tool calls

1. `morletdb.list_collections()`  
   → public.seismic: 77 recordings, 135617113 samples

2. `seismo.list_stations()`  
   → PB01, PB02, PB06, PSGCX

3. `seismo.catalog_events(start_utc="2025-03-14T20:50:00Z", end_utc="2025-03-14T21:15:00Z", station="PB01")`  
   → 1 events: 2025-03-14T21:02:50 M2.6 Offshore Tarapaca, Chile, P at PB01 21:03:10

4. `morletdb.locate_time(collection="public.seismic", utc_time="2025-03-14T21:03:10Z", metadata_filter="{\"station\": \"PB01\"}")`  
   → CX.PB01..HHZ.20250312T010454 sample 24469588

5. `morletdb.list_recordings(collection="public.seismic", metadata_filter="{\"station\": \"PB01\"}")`  
   → {
  "collection": "public.seismic",
  "total": 17,
  "offset": 0,
  "recordings": [
    {
      "recording_id": "CX.PB01..HHZ.20250310T235954",
      "sample_rate_hz": 100.0,
      "channel": "HHZ",
 

6. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24465588, end_sample=24477588, min_frequency_hz=2)`  
   → onsets: 21:03:07.27 (ratio 5.53), 21:03:09.11 (ratio 1822.33), 21:03:30.48 (ratio 9.94)

7. `morletdb.compare_intervals(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24469300, end_sample=24473396, bands="0.1-1,1-2,2-5,5-10,10-20,20-50")`  
   → 9.44 dB above median of 28 windows; excess by band: 0.1-1 Hz 0.01, 1-2 Hz 0.07, 2-5 Hz 0.37, 5-10 Hz 0.49, 10-20 Hz 0.07, 20-50 Hz 0.00

8. `morletdb.describe_interval(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24469300, end_sample=24473396, bands="0.1-1,1-2,2-5,5-10,10-20,20-50", min_frequency_hz=1)`  
   → peak 2797.0, crest 3.3, dominant 5.9 Hz, centroid 5.7 Hz

9. `morletdb.read_interval(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=24469000, end_sample=24475000, max_points=120)`  
   → 60 min/max buckets of 100 samples from 2025-03-14T21:03:04; peak 2797.0

*Claude:* The event is clear at PB01: P arrives about 21:03:09 and S about 21:03:30, roughly 30 dB above background. Next I'm searching the archive for similar signals.

10. `morletdb.search_from_time(collection="public.seismic", utc_time="2025-03-14T21:03:09Z", metadata_filter="{\"station\": \"PB01\"}", min_frequency_hz=2, look_seconds=10, top_k=15)`  
   → 15 hits: CX.PB02..HHZ.20250312T021305 2025-03-13T01:58:29 d=0.1085; CX.PB02..HHZ.20250310T235959 2025-03-11T11:50:18 d=0.109; CX.PSGCX..HHZ.20250312T005629 2025-03-14T07:02:13 d=0.1104; CX.PB01..HHZ.20250312T010454 2025-03-13T05:42:49 d=0.1124; CX.PB02..HHZ.20250310T235959 2025-03-11T09:36:10 d=0.1152; CX.PB02..HHZ.20250312T021305 2025-03-14T17:10:37 d=0.1154; CX.PSGCX..HHZ.20250312T005629 2025-03-14T13:10:51 d=0.1167; CX.PB01..HHZ.20250312T010454 2025-03-13T05:26:26 d=0.1169; CX.PB01..HHZ.20250312T010454 2025-03-14T01:06:26 d=0.1173; CX.PB02..HHZ.20250312T021305 2025-03-14T16:57:18 d=0.1198; CX.PB02..HHZ.20250312T021305 2025-03-14T07:59:01 d=0.1225; CX.PB06..HHZ.20250312T015937 2025-03-13T08:55:47 d=0.1236; CX.PB02..HHZ.20250312T021305 2025-03-12T16:08:19 d=0.1253; CX.PB06..HHZ.20250312T015937 2025-03-14T00:30:21 d=0.1287; CX.PB06..HHZ.20250310T235955 2025-03-11T15:34:50 d=0.1301

11. `seismo.catalog_events(station="PB01")`  
   → 52 events

12. `seismo.catalog_events(station="PB02")`  
   → 52 events

13. `seismo.catalog_events(station="PSGCX")`  
   → 52 events

14. `seismo.catalog_events(station="PB06")`  
   → 52 events

*Claude:* Eight of the 15 hits line up with catalogued events. The strongest hit, at PB02 on 13 March around 01:58:30, has no catalogue entry, so I'm checking it at the other stations.

15. `morletdb.locate_time(collection="public.seismic", utc_time="2025-03-13T01:57:50Z")`  
   → CX.PB01..HHZ.20250312T010454 sample 8957588; CX.PB02..HHZ.20250312T021305 sample 8548465; CX.PB06..HHZ.20250312T015937 sample 8629279; CX.PSGCX..HHZ.20250312T005629 sample 9008083

*Claude:* Window 01:57:50–01:59:50 (12000 samples) at all four stations:

16. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB02..HHZ.20250312T021305", start_sample=8548465, end_sample=8560465, min_frequency_hz=2)`  
   → onsets: 01:58:26.51 (ratio 187.44), 01:58:41.71 (ratio 31.91)

17. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=8957588, end_sample=8969588, min_frequency_hz=2)`  
   → onsets: 01:58:22.46 (ratio 9144.73), 01:58:34.73 (ratio 48.6)

18. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PB06..HHZ.20250312T015937", start_sample=8629279, end_sample=8641279, min_frequency_hz=2)`  
   → onsets: 01:58:39.90 (ratio 14.38), 01:59:03.83 (ratio 6.92)

19. `morletdb.find_onsets(collection="public.seismic", recording_id="CX.PSGCX..HHZ.20250312T005629", start_sample=9008083, end_sample=9020083, min_frequency_hz=2)`  
   → onsets: 01:58:34.14 (ratio 1321.91)

20. `seismo.predict_arrivals(origin_utc="2025-03-13T01:58:05.7Z", latitude=-21.05, longitude=-68.95, depth_km=105)`  
   → PB01 P 01:58:22 S 01:58:34; PB02 P 01:58:25 S 01:58:41; PB06 P 01:58:35 S 01:58:58; PSGCX P 01:58:36 S 01:58:59

21. `seismo.predict_arrivals(origin_utc="2025-03-13T01:58:05.7Z", latitude=-20.95, longitude=-68.95, depth_km=100)`  
   → PB01 P 01:58:21 S 01:58:33; PB02 P 01:58:25 S 01:58:41; PB06 P 01:58:36 S 01:58:59; PSGCX P 01:58:34 S 01:58:57

22. `morletdb.compare_intervals(collection="public.seismic", recording_id="CX.PB01..HHZ.20250312T010454", start_sample=8960600, end_sample=8964696, bands="0.1-1,1-2,2-5,5-10,10-20,20-50")`  
   → 12.94 dB above median of 28 windows; excess by band: 0.1-1 Hz 0.00, 1-2 Hz 0.00, 2-5 Hz 0.04, 5-10 Hz 0.24, 10-20 Hz 0.62, 20-50 Hz 0.10

## Answer

# The 14 March 2025 offshore Tarapacá earthquake at PB01, and what matches it

## 1. The event at PB01

The recording is `public.seismic`, `CX.PB01..HHZ.20250312T010454`, starting at sample 24469289.

The catalogue entry is EMSC `20250314_0000238`: ML 2.6, origin 21:02:50.6 UTC, at 20.63°S 70.52°W, 10 km deep, 117 km from PB01.

- **P wave:** sharp arrival at **21:03:09.0** (sample ≈24469489), with an onset strength of about 1,800. The predicted time was 21:03:10.8.
- **S wave:** a larger arrival at **21:03:30.5** (sample ≈24471637), peaking at 2,797 counts.
- **The signal:** a 41-second window is **+9.4 dB** above the background median overall and **+31 to +35 dB** in the 2–20 Hz bands. Below 1 Hz (the ocean microseism) nothing changed. 85% of the new energy is at **2–10 Hz**, with the dominant frequency at about 5.9 Hz. The waveform is a clear P arrival, a coda, a stronger S arrival, and a decay over about 30 s.
- **Mismatch with the catalogue:** the S−P time I observe is about **21 s**. The catalogue location predicts 14.7 s, which puts the source nearer 150–170 km away than 117 km. Offshore locations of small events like this one are often poorly constrained, so I'd take the catalogue epicentre as approximate.

## 2. Similar signals in the archive

I searched from the event time with a 2 Hz high-pass across all four stations. All 15 hits were strong, at distances of 0.11–0.13. I checked each one against the catalogue's predicted arrivals at its station:

| # | Station | Hit start (UTC) | Dist. | Catalogue |
|---|---|---|---|---|
| 1 | PB02 | 03-13 01:58:29.8 | 0.109 | **none** |
| 2 | PB02 | 03-11 11:50:18.6 | 0.109 | ✔ M3.5 Tarapacá, 120 km deep |
| 3 | PSGCX | 03-14 07:02:13.5 | 0.110 | **none** |
| 4 | PB01 | 03-13 05:42:50.0 | 0.112 | ✔ M2.5, 107 km deep |
| 5 | PB02 | 03-11 09:36:10.0 | 0.115 | ✔ M2.7, 120 km deep |
| 6 | PB02 | 03-14 17:10:37.5 | 0.115 | **none** |
| 7 | PSGCX | 03-14 13:10:51.9 | 0.117 | ✔ M3.0 Tarapacá |
| 8 | PB01 | 03-13 05:26:26.9 | 0.117 | **none** |
| 9 | PB01 | 03-14 01:06:26.3 | 0.117 | ✔ M3.0, 111 km deep |
| 10 | PB02 | 03-14 16:57:18.8 | 0.120 | ✔ M3.1 Antofagasta |
| 11 | PB02 | 03-14 07:59:01.8 | 0.123 | ✔ M4.5 Antofagasta |
| 12 | PB06 | 03-13 08:55:47.4 | 0.124 | **none** |
| 13 | PB02 | 03-12 16:08:19.9 | 0.125 | **none** |
| 14 | PB06 | 03-14 00:30:21.7 | 0.129 | **none** |
| 15 | PB06 | 03-11 15:34:50.6 | 0.130 | ✔ M2.8 Antofagasta |

**8 of the 15 hits line up with catalogued earthquakes**, all within a few seconds of the predicted P or S arrival. The other 7 are not in the catalogue.

## 3. The strongest uncatalogued hit: PB02, 13 March around 01:58:30

I ran the onset detector at all four stations between 01:57:50 and 01:59:50 (2 Hz high-pass):

| Station | P (obs.) | S (obs.) | S−P | Trial-location P / S−P |
|---|---|---|---|---|
| PB01 | 01:58:22.5 (onset strength ≈9,100) | 01:58:34.7 | 12.3 s | 21.5 / 12.2 s |
| PB02 | 01:58:26.5 (≈190) | 01:58:41.7 | 15.2 s | 25.6 / 15.4 s |
| PSGCX | 01:58:34.1 (≈1,300) | none detected | — | 34.9 |
| PB06 | 01:58:39.9 (≈14, weak) | 01:59:03.8 | 23.9 s | 36.2 / 23.7 s |

**It is a real earthquake, missing from the EMSC snapshot.** Four things point that way:

- **Seen everywhere:** it appears at all four stations, which are up to about 350 km apart. A glitch or local noise at one station would not.
- **Consistent arrival order:** the arrivals move out by 17 s across the network, and the S−P time grows steadily with distance (12 → 15 → 24 s). That pattern only comes from a single source.
- **A location fits:** a trial hypocentre at about **20.95°S 68.95°W, ~100 km deep, origin ≈01:58:05.7 UTC** fits P at PB01, PB02 and PSGCX within about 1 s, and S−P at all three measured stations within 0.4 s. PB06's P detection is about 3.7 s late, and its S about 4 s late, against this trial location. PB06's onset is weak (≈14 against thousands at PB01), so the detector probably picked late in the P coda. The location also isn't refined; I only hand-tested two trial points.
- **Fits the known activity:** that spot is in the intermediate-depth slab cluster under inland Tarapacá. Several catalogued events in these four days sit there, for example 03-13 05:42 at 21.17°S 68.93°W, 107 km deep, which appears in the table as hit 4.

At PB01 the signal is +12.9 dB overall and +44 dB at 10–20 Hz, peaking at 5,552 counts. That's higher in frequency and amplitude than the target event, as you'd expect from a source about 57 km away with less high-frequency loss along the path. Judging only by its amplitude at roughly half the distance of the M2.6, I'd guess it is somewhere around M2–3, but I haven't calibrated that and it is only a guess.

**Citation:** `public.seismic`, `CX.PB02..HHZ.20250312T021305`, samples 8552448..8556544 (the search hit). At PB01, the event is at `CX.PB01..HHZ.20250312T010454` from sample 8960835 onward.

## Caveats

- Arrival times come from an automatic detector, not hand picks, and the location is a two-point trial fit, not an inversion.
- I didn't check the other six uncatalogued hits (3, 6, 8, 12, 13, 14). They could be more missing small events, or the S wave of a catalogued event landing in a different window; each would need the same check across all four stations.
- The catalogue is only a snapshot of EMSC, which may simply leave out small deep events that the national network (CSN) does list.
