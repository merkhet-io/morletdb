# A model-driven session (not blind)

[session_trace.jsonl](session_trace.jsonl) records an investigation of the README question in which a language
model chose every tool call: Claude Opus 5.5 in Claude Code, on 2026-11-15, acting as the model through the two
read-only MCP servers of this demo (`graph` and `morletdb`), connected as `fleet_reader`. One line per call: the
server, tool, arguments, whether it failed, the time it took and the full result.

**What it is not.**
- It is **not blind**. The model had seen the scripted reference ([flagship_trace.json](flagship_trace.json)) before
  the session, so the session shows that the tools are enough to reach and check the answer, not that a model finds
  it unaided.
- It is **not [agent.py](../agent.py)**. The calls went through a small one-call MCP client, not the Anthropic API
  loop. A blind `agent.py` run is still to be done.

16 calls, none failed, 7.2 s in tool calls in all. The model chose:

| Step | Calls | Why |
|---|---|---|
| 1 | `graph.plant_overview` | which units are monitored, and where |
| 2–5 | `graph.installed_at`, the four P-102 positions | which bearing runs in each position, and since when |
| 6–9 | `morletdb.trend`, the four positions since the install | which position changed |
| 10 | `graph.similar_failures`, P-102.BRG-3, `top_k` 10 (the tool default) | have we seen this before |
| 11 | `graph.failure_history`, the precedent bearing | what it was and what fixed it |
| 12 | `graph.same_lot` | who shares the risk |
| 13 | `graph.spare_stock` | what can replace it |
| 14–16 | `morletdb.envelope_spectrum`: now, the precedent, and BRG-3's second day | does the defect frequency support the hypothesis |

## The answer, as given in the session

- **What changed:** all four P-102 positions rose over the last two days, BRG-3 the most. Its latest RMS is 0.145
  against a median of 0.066 in its first days after installation (2.2×), and its 2.5–5 kHz band has been up since
  04-14. The other positions are at 1.3–1.6×, so something pump-wide may also be involved.
- **Seen before?** Yes. All 10 most similar earlier windows come from bearing SN-0311-001 in P-102.BRG-1, recorded
  1–2 days before its outer-race failure (FL-2004-02-P102-BRG1). This is the same precedent as the scripted run at
  `top_k` 50.
- **Outer-race defect?** The evidence points that way; it is not a diagnosis. BRG-3's envelope spectrum shows a
  231 Hz family with harmonics at 463, 694 and 926 Hz (22.7 dB above the median). The failed bearing showed the same
  family before it failed (36 dB), and BRG-3 had none on its second day. 231 Hz is close to the outer-race defect
  frequency published for this bearing (about 236 Hz). An inspection is needed to confirm it.
- **What fixed it last time:** WO-2004-0219-01, an overhaul replacing all four bearings. The unit was out of service
  from 02-19 to 03-04 waiting for parts; the replacements came from LOT-0402.
- **Who shares the risk (LOT-0402, Distributor 2):** P-102 BRG-1, 2 and 4 (monitored, 1.3–1.6×); F-113 BRG-1 and
  P-104 BRG-2 at Site A, F-214 BRG-3 and P-203 BRG-4 at Site B (not monitored: the graph is the only evidence).
- **Spares:** North works 3 on hand (reorder point 2), two of them LOT-0402; only SN-0301-013 is from another lot.
  South works 3, LOT-0405. A four-bearing overhaul of P-102 needs stock from South works or an order.

**Differences from the scripted reference:** `top_k` 10 instead of 50; the envelope-spectrum check, which the script
does not make; and the remark that the rise is pump-wide.
