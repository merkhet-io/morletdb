# Policy engine for episodic memory

Decides which episodes move down the compaction ladder; the database applies the moves with `morlet_set_level`
and records them in the ledger ([design](../../docs/memory-design.md)). Plain Python, deterministic for a given
input and seed, so policies can be compared in study F1.

- `policy.py`: episodes and moves; **filters** (vetoes: exempt, minimum age, protected context), the **keep-value**
  (`v = w_n·novelty + w_i·importance + w_u·use + w_c·context_rarity − w_a·age`, versioned, written to the ledger
  with every move) and the four F1 policies (recency, random, novelty, novelty + importance); **selectors** (budget,
  manual) decide why a run happens, the **ladder picker** decides what moves (lowest value first, one level step at a
  time, until the bytes are freed), **k-medoids** finds real representatives for level 2; a **task** runs a dry run
  or applies the moves in batches. The structure follows the compaction design of RisingWave's Hummock storage
  engine (Apache-2.0); the code is our own.
- `store_pg.py`: the PostgreSQL store; every change goes through the extension's SQL functions, size estimates are
  dry runs inside a rolled-back transaction.
- `test_policy.py`: unit tests against an in-memory store (`python -m unittest discover -s tools/memory`).

Novelty needs a distance between episodes. For waveforms it is the search distance (bounded-lag envelope distance),
computed with the extension's exact search; the engine takes it as a function, so experiments can supply it.
