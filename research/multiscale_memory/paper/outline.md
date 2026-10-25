# Paper outline (working; claims decided only after evidence)

## 1. Problem and scope
Retrieval-augmented signal models give a model historical examples as context. We ask
whether the **representation** of retrieved signal context matters at a fixed context
budget, holding the retrieved examples and the model capacity constant. We also ask
whether a signal database should store and serve such representations. Scope: 1D signals
and single-channel windows; the first evidence comes from public ECG (PTB-XL), not from
industrial data.

## 2. Related work and precise contribution
- Retrieval-augmented time-series models: TS-RAG, TimeRAG, RAG4CTS.
- Wavelet tokenization: the wavelet ViT tokenizer.
- Spectral and operator learning: operator-based machine intelligence (inspiration only).
- Classic DWT time-series indexing and wavelet synopses (citations to verify).
- Contribution candidates, to be decided after the results:
  - (a) a controlled representation-vs-selection study at matched budgets, and/or
  - (b) a systems study of representation storage and selective reads (H3), and/or
  - (c) a negative result.

## 3. Representation and retrieval method
An orthonormal DWT with explicit boundary handling. Immutable specs with content ids,
level-major storage, coarse context plus deterministic refinement, and exhaustive
retrieval as the oracle.

## 4. Experimental protocol
See `../PROTOCOL.md`: patient-disjoint folds, training-only gallery, fixed retriever,
budget-matched arms, seeds, and a patient-level paired bootstrap.

## 5. Results and ablations
*Empty until the frozen protocol has been run. Tables are generated from
`results/*/evaluation.json`.*

## 6. Limitations, reproducibility and validity threats
- One clinical dataset with a single lead.
- A small model and a fixed retriever.
- Neighbour labels may dominate the signal context.
- No industrial data, and H3 is untested.
