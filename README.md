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
dominance-weight profile it reads at replay. Only the performance flavour
exists so far; a control-weighted rating and a separate excitement rating
are planned.

The system rebuilds from seed on every run: it replays the full ordered
ledger of bouts through the engine, so the stored ratings are always a
clean function of the bout record and cannot drift. New events are fetched
from the daily-refreshed Greco1899 mirror of ufcstats.com and appended to
the ledger; re-running is safe (a bout already in the ledger is never
folded twice).

## Layout

```
TH_UFC_PR/
  glicko_engine.py       # the Glicko-2 engine (pure maths, no state, no I/O)
  scoring.py             # continuous-S dominance score from raw per-fighter stats
  runner.py              # replay the ledger -> rating history + current ratings
  ingest.py              # fetch new results/stats and append them to the ledger
  update.py              # one-step: fetch, fold, rebuild, write (the "rerun" core)
  build_seed_ledger.py   # (re)build the seed ledger from the frozen reference files
  anchors/
    performance.yaml     # dominance-weight profile for the performance flavour
  data/
    bout_ledger.parquet  # the live, append-only bout ledger (grows per event)
    rating_history.parquet   # replay output (written by runner/update)
    ratings_current.parquet  # replay output (written by runner/update)
  reference/             # frozen MSc artefacts, read-only, for the regression gate
    frozen_bout_pairs.parquet
    frozen_fights_per_fighter.parquet
    frozen_glicko_history.parquet
    frozen_glicko_current.parquet
  docs/
    UFC_Live_Ratings_Project_Definition.md
    UFC_Live_Ratings_Ledger_Schema.md
  tests/
    reproduce_frozen.py  # regression gate: reproduces the frozen ratings bit-exactly
```

## Two ledgers, kept separate

The **seed** is the frozen 11 July 2026 snapshot. It is the immutable
reproduction baseline, reconstructed in memory from `reference/` by
`build_seed_ledger.build_ledger_df()`, and it is what the regression gate
checks. The **live ledger** (`data/bout_ledger.parquet`) is the growing one
that `update.py` extends after each event. Keeping them separate is what
lets the gate stay meaningful while the live ratings move on.

## Current state

Built and validated bit-for-bit against the frozen MSc ratings:

- `glicko_engine.py`: Glicko-2 engine, extracted from notebook 11 and
  checked against the Glickman (2013) worked example.
- `scoring.py`: continuous-S score derived from raw per-fighter totals;
  reproduces the frozen scores exactly.
- `runner.py`: replays the ledger; reproduces the frozen ratings exactly.
- `ingest.py` / `update.py`: fetch and fold new events; proven on the eight
  events between the freeze and early September (all with full stat
  coverage), and idempotent on re-run.
- `tests/reproduce_frozen.py`: the regression gate, passing, and
  independent of the live ledger.

Still to come: the Streamlit dashboard with a rerun button; display-time RD
decay in the ranked view; the control-weighted flavour; the excitement
index; hardening of fighter name-resolution for returning fighters; and
scheduled automation.

## Running it

Dependencies: `pandas`, `pyarrow`, `pyyaml` (the engine itself needs only
the standard library).

```bash
# prove the historical reproduction still holds (reads only reference/)
python tests/reproduce_frozen.py        # prints a table of zeros, exits 0

# fetch new events, fold them in, rebuild, write outputs
python update.py

# rebuild ratings from the current ledger without fetching
python runner.py

# re-initialise the live ledger back to the frozen seed
python build_seed_ledger.py
```

In Colab, clone the repo and run the same commands; nothing is tied to a
drive, since all paths are relative to the repo root.

## Design constraints

- **GitHub is the source of truth.** All code is drive-agnostic; paths are
  relative to the repo root, never hardcoded.
- **Reproducibility is a design constraint.** Each post-event update is a
  single, deterministic step. Replay is bit-reproducible via a stable sort;
  idempotency is a `bout_id` membership check on append.
- **Rebuild-from-seed.** Ratings are derived on every run, never carried
  forward and mutated.

## Documentation

- `docs/UFC_Live_Ratings_Project_Definition.md`: what this is, the roadmap,
  success tests, and scope boundaries.
- `docs/UFC_Live_Ratings_Ledger_Schema.md`: the bout-ledger schema and the
  technical detail behind the summary above.
