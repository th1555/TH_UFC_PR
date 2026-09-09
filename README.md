# TH_UFC_PR

Live-updating UFC fighter ratings. This is the post-submission, personal
build-out of the competitive axis from the MSc project (a Glicko-2 rating
with a continuous-S dominance modifier), turned into a system that
recomputes after each UFC event so the ranking is current rather than a
frozen snapshot.

It is a clean, separate repo by design. The submitted MSc work
(`th1555/ufc-fighter-value-mapper`) is read-only reference; this repo is
the live working tree and is not tied to it.

## What it does

One engine produces several ratings ("flavours") by swapping the
dominance-weight profile it reads at replay:

- a pure-performance rating (first),
- a control-weighted rating (a profile swap),
- and, as a separate object rather than a Glicko flavour, an excitement
  rating (later).

Ratings update per event. The system rebuilds from seed on every run: it
replays the full ordered list of bouts through the engine, so the stored
state is always a clean function of the bout record and can never drift.

## Layout

```
TH_UFC_PR/
  glicko_engine.py     # the rating calculator (done; self-test passing)
  runner.py            # the replay loop (next to build)
  anchors/             # dominance-weight profiles, one YAML per flavour
    performance.yaml
    control.yaml
  data/                # committed on purpose (versioned provenance)
    bout_ledger.parquet    # the append-only bout record
    rating_history.parquet # the replay output
  update.ipynb         # the "one button": pull, run, push
  docs/
    UFC_Live_Ratings_Project_Definition.md
    UFC_Live_Ratings_Ledger_Schema.md
```

`anchors/` and `data/` are populated as those pieces are built; only the
engine exists so far.

## Current state

- `glicko_engine.py`: pure Glicko-2 engine, extracted verbatim from
  notebook 11 and validated against the Glickman (2013) worked example.
  It holds no state, reads no files, and knows nothing about UFC data; it
  updates one fighter for one fight given an already-computed outcome.
- Everything else (the ledger and its ingestion, the score-from-raw
  computation, the replay loop, the history output) is still to build.
  The runner is next.

## Running the engine self-test

No UFC data required; this only confirms the engine's arithmetic.

Locally:

```bash
python glicko_engine.py    # prints the Glickman check; exits 0 on pass
```

In Colab (work against the repo, not a mounted drive):

```python
!git clone https://github.com/th1555/TH_UFC_PR.git   # adjust owner if different
%cd TH_UFC_PR
import glicko_engine
print(glicko_engine.run_glickman_example())          # True on pass
```

## Design constraints

- **GitHub is the source of truth.** All code is drive-agnostic: paths are
  relative to the repo root, never hardcoded to a particular drive.
- **Reproducibility is a design constraint.** Each post-event update must
  be a single, simple, deterministic step; a run that needs a manual
  fiddle means the design has failed.
- **Rebuild-from-seed.** State is a derived artefact, never carried
  forward and mutated. Idempotency is a `bout_id` membership check on
  append; a stable sort makes replay bit-reproducible.

## Documentation

- `docs/UFC_Live_Ratings_Project_Definition.md`: what this is, the rough
  roadmap, success tests, and scope boundaries.
- `docs/UFC_Live_Ratings_Ledger_Schema.md`: the bout-ledger schema and the
  full technical detail behind the summary above.
