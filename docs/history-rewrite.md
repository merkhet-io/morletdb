# History rewrite (2026-10-25)

Before the repository became public, its history was rewritten so that every
commit is authored and committed as `Zin <einparameteruntergruppe@gmail.com>`
(commits made with GitHub's merge button keep `GitHub <noreply@github.com>`).
Only author/committer metadata changed: **every commit's file tree is
byte-identical** to its original, verified commit by commit. Commit SHAs changed.

- The original history, its pull requests (#1–#27) and the `v0.1.0-alpha.1`
  release live in the private archive repository `merkhet-io/morletdb-archive`.
  PR numbers in commit messages before this rewrite refer to that archive.
- Reports and docs written before the rewrite record original SHAs (for example
  the seismic evaluation's code revisions). Use the table below to find the
  rewritten commit with the identical tree; the recorded evidence is unchanged.
- Only `main`, `docs/readme-brand-refresh` and the tag were carried over; other
  branches remain in the archive.

| Date | Original | Rewritten | Subject |
|---|---|---|---|
| 2026-10-23 | `00e470e1bad2` | `e5ad5e4c1bdf` | Restructure monorepo and scaffold Rust PostgreSQL extension (#4) |
| 2026-10-23 | `0150092626e0` | `89e9ad47b4b9` | Merge pull request #15 from merkhet-io/refactor/canonical-stream-workflow |
| 2026-10-23 | `1d4f3271d291` | `80d94722f376` | Add domain-neutral recordings and exact waveform interval search (#2) |
| 2026-10-23 | `345eb099148e` | `fef78c27500a` | Install build-only Postgres server headers for container packaging |
| 2026-10-23 | `390225b68c74` | `4e071c300db6` | Batch signal ingestion, benchmark continuous recordings and refine branding (#13) |
| 2026-10-23 | `3f9637185b61` | `db6a5f329cd3` | Persist binary recordings and rebuildable waveform feature indexes (#3) |
| 2026-10-23 | `4084b79e488f` | `25fafb442ef2` | Add stored interval queries, source exclusion and overlap suppression (#8) |
| 2026-10-23 | `568beb5d6424` | `9005df1a5754` | Initialize MorletDB repository |
| 2026-10-23 | `602f1b4742b8` | `7639b3ef5120` | Decode precisely the checksummed evaluation input bytes |
| 2026-10-23 | `6daf9ede7f14` | `6d22f68b6c0a` | Build MorletDB CNC monitoring MVP with Apache 2.0 licensing |
| 2026-10-23 | `6eee052e4f79` | `e68582e1bdc3` | Persist versioned Morlet segments and search stored features (#7) |
| 2026-10-23 | `7e19d5b03f5f` | `9fa0297d9720` | Merge pull request #16 from merkhet-io/feat/recording-interval-workflow |
| 2026-10-23 | `7edd1f105720` | `f73f48378ee4` | Add continuous chunk extraction and typed segment benchmarks (#10) |
| 2026-10-23 | `a4d5a7c8b1fb` | `60fd6a2e7075` | Port Morlet extractor to Rust with Go parity fixtures and SQL APIs (#5) |
| 2026-10-23 | `ad80f9c0fb6c` | `48424adb124d` | Merge pull request #17 from merkhet-io/feat/retrieval-evaluation |
| 2026-10-23 | `be3ba3fcfc29` | `d83ccbb63b0d` | Use the pinned pgrx package command release default |
| 2026-10-23 | `bfd40410208b` | `7b29563fe751` | Merge packaging correction from the interval workflow |
| 2026-10-23 | `c52a68f6d51a` | `e33a02c7beeb` | Add durable chunk ingestion and restartable extraction checkpoints (#11) |
| 2026-10-23 | `c89ca04e3b3c` | `0960114bd54a` | Add transactional Postgres collections and exact window search (#6) |
| 2026-10-23 | `d035425acaba` | `7edad736ef81` | Add resumable batched Parquet ingestion for continuous recordings (#12) |
| 2026-10-23 | `d1760ff2661f` | `92fe1f85a0b0` | Add real-recording relevance evaluation and exact-search scale experiments |
| 2026-10-23 | `d9caa84681f6` | `b5a97c67e56b` | Consolidate active development on Rust and continuous recordings |
| 2026-10-23 | `ef2002806c45` | `641dec98fc7f` | Complete the recording interval search and inspection workflow |
| 2026-10-23 | `ef936d9e3e44` | `cb673f1d76eb` | Add reproducible ingestion and analytical retrieval benchmarks (#9) |
| 2026-10-24 | `0594ee072bfb` | `1b3f86dcfedf` | Prepare and publish the first tested MorletDB alpha (#18) |
| 2026-10-25 | `035eddcd2012` | `3d3f1fbca062` | Quote Mermaid labels so GitHub renders the pipeline diagram |
| 2026-10-25 | `0b4123337807` | `3dd011bec080` | Serialize seismic database setup; document fresh-install requirement |
| 2026-10-25 | `1ffe18303c4f` | `862eb72f93e7` | Merge pull request #26 from merkhet-io/feat/seismic-replay-investigation |
| 2026-10-25 | `26cc48e982ed` | `bff1ea723f73` | Replace Mermaid with a branded pipeline diagram |
| 2026-10-25 | `2c1693d09abe` | `52c5f8b9dbd8` | Document condition-monitoring origins and CNC spindle use case (#25) |
| 2026-10-25 | `321a3664f879` | `0f4d71c226f3` | Add seismic UI, real-data evaluation report, screenshots and findings |
| 2026-10-25 | `49a0b6161c03` | `e99ed12d40df` | Collect prioritized demo ideas, evidence gates and dataset access boundaries (#24) |
| 2026-10-25 | `54bd9eb272a6` | `b0f434820b04` | Add seismic replay pipeline: dataset cache, adapters, ingestion, STA/LTA, retrieval |
| 2026-10-25 | `60105bcc896c` | `9fe054410c16` | Scope detector closure to a run's own streams; add deterministic re-detection |
| 2026-10-25 | `606f75bb8767` | `0a7213ded6b4` | Fix seismic version identity, progress-only flushes and detector stop/resume |
| 2026-10-25 | `6a829a209a6a` | `518c1a562bd9` | Commit seismic version identities atomically with their samples |
| 2026-10-25 | `72e8d722c72a` | `ec4de1c3dcfe` | Add visual notebook for waveform search and match inspection (#19) |
| 2026-10-25 | `7961016ccb87` | `cc7deb80a63d` | Refresh README for public release |
| 2026-10-25 | `8642e0667cf5` | `db8fa701f769` | docs: design wave-token indexes and advanced signal retrieval (#21) |
| 2026-10-25 | `9336de17361e` | `5912ae352f13` | Present the seismic application as a repository example |
| 2026-10-25 | `a33477b9e6ad` | `d5bdc4bd3447` | Explain waveform investigation, wave-index evidence and defense evaluation direction (#23) |
| 2026-10-25 | `a371b60cce0f` | `e0ef11dbfa5a` | Merge remote-tracking branch 'origin/main' into feat/seismic-replay-investigation |
| 2026-10-25 | `a5b84a6517cb` | `b79f8ce6f5ed` | Rerun seismic evaluation at a clean commit on a fresh database (v2) |
| 2026-10-25 | `aa270b16fb55` | `22b02ff74e7c` | Add explicit morlet-energy-lowrate-v1 representation for native seismic rates |
| 2026-10-25 | `ce6d2481d0ca` | `5ef0134d0244` | Remove legacy Go runtime and isolate frozen numerical reference (#20) |
| 2026-10-25 | `d5776f679873` | `0545a98199a2` | Regenerate seismic v2 report at 518c1a5 on a fresh database |
| 2026-10-25 | `e265709fdab3` | `8542ca6673bf` | Bound morlet_read_interval chunk lookup for long recordings |
| 2026-10-25 | `f051fce00f7d` | `332ea5331d13` | PoC: temporal wave-cell retrieval and approximate posting index (#22) |
