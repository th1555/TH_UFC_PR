# UFC Live Ratings: Bout Ledger Schema

Reference for the post-submission, live-updating extension of the UFC
fighter-value work. This document is deliberately self-contained so it
remains usable if separated from the project context.

Status: design agreed, not yet implemented. Last updated 2026-09-09.

---

## 1. Context

The MSc project (competitive axis via Glicko-2 with a continuous-S
dominance modifier, plus a public-profile axis) is submitted and frozen.
This is a personal build-out that turns the frozen competitive axis into
a live system: ratings that update after each UFC event, exposed as
several filterable "flavours" produced by swapping the dominance-weight
profile.

The governing constraint is reproducibility. Each post-event update must
be a single, simple, repeatable step. A run that needs a manual fiddle
means the design has failed.

## 2. Locked design decisions

These four decisions shape the schema and should be read before it.

1. **Rebuild-from-seed.** Every update re-runs the full ordered bout
   sequence from the seed, including the new events. State is a derived
   artefact, never carried forward and mutated. At this data scale
   (~8,580 modelable bouts) a full replay costs seconds, so this removes
   most incremental-update failure modes for free. True incremental
   resume is a later optimisation only if runtime ever becomes a problem.

2. **Raw facts in the ledger, not derived scores.** The ledger stores
   raw fight facts. The continuous-S SCORE is a function of the chosen
   dominance-weight profile, so it is computed by the runner at replay
   time, never baked into the ledger. Baking it in would contaminate the
   immutable input with a flavour-specific value and force a ledger
   rewrite every time a new flavour is added.

3. **Raw per-fighter totals, not pre-computed differences.** The
   dominance score works on differences (winner minus loser), but the
   ledger stores each fighter's raw per-bout totals and lets the runner
   derive differences. Reason: a difference already taken cannot be
   re-weighted differently by another flavour, and it destroys the
   per-fight sums (combined strike volume, total knockdowns) that a
   future excitement index depends on. Raw totals serve the dominance
   diffs, the control re-weighting, and the excitement sums from one
   faithful record.

4. **Single history file, snapshots as filters.** A full per-bout rating
   history is persisted once. "Ratings as of event X" is a query on that
   history (bouts on or before X, latest post-fight row per fighter), not
   a separate stored artefact. No per-event snapshot files.

Flags (`modelable`, `stats_coverage`) are stored in the ledger, not
applied as a pre-filter, so the ledger stays a complete auditable record
and the runner applies the logic at replay.

## 3. The three artefacts

| artefact | role |
|---|---|
| **Bout ledger** | immutable, append-only, ordered list of raw bout facts; the single source of truth (Section 4) |
| **Anchor profile** | one small YAML per flavour, holding the dominance weights the runner reads at replay (Section 6) |
| **Rating history** | the replay output; one row per fighter-bout with post-fight rating state, plus a run-meta provenance stamp (Section 7) |

## 4. Bout ledger schema

One row per bout.

| column | dtype | notes |
|---|---|---|
| `bout_id` | string | canonical URL-hash ID; primary key and idempotency key |
| `event_id` | string | |
| `event_date` | datetime64 | stable sort runs on (`event_date`, `bout_id`) |
| `bout_order` | Int64 | position on the card, nullable; carried for later use (display, card-position context), not read by the engine |
| `fighter_1_id` | string | raw participant, not pre-resolved to winner |
| `fighter_2_id` | string | raw participant |
| `outcome` | category | raw code: `F1_WIN`, `F2_WIN`, `DRAW`, `NC`, `OVERTURNED`, `CNC`, `OTHER` |
| `method` | string | raw string ("KO/TKO", "Decision - Unanimous"); runner maps to a baseline key and derives the decision-versus-finish category |
| `weightclass` | string | canonical bout weight class (e.g. "Lightweight"); seed from the frozen base, new events canonicalised from the feed; drives the per-division boards |
| `finish_round` | Int64 | nullable; NA for decisions |
| `finish_time_seconds` | Int64 | nullable |
| `sig_strikes_landed_1` / `_2` | Int64 | per-fighter bout totals, summed over rounds |
| `sig_strikes_attempted_1` / `_2` | Int64 | |
| `total_strikes_landed_1` / `_2` | Int64 | kept for the excitement index |
| `takedowns_landed_1` / `_2` | Int64 | |
| `sub_attempts_1` / `_2` | Int64 | |
| `knockdowns_1` / `_2` | Int64 | |
| `control_seconds_1` / `_2` | Int64 | mm:ss converted to seconds at ingest |
| `modelable` | boolean | computed at ingest from `outcome` and `method`; stored not pre-filtered; recomputable from raw columns |
| `stats_coverage` | boolean | true when the per-fighter stat fields are all present; gates continuous versus binary scoring |
| `source` | string | e.g. `greco1899` |
| `ingest_snapshot` | string | the pull the row entered on; feeds "current as of event X" provenance |

## 5. Semantic rules

- **Nullable integers, not zero-fill.** Counts use pandas `Int64`
  (nullable). A fighter who genuinely landed zero is a `0`; a bout with
  no round-level record is `NA`. `stats_coverage` reads off this
  distinction. This is the same true-zero-versus-missing rule used on the
  public-profile side: a true zero is a low value; a missing value is
  dropped, never zero-filled.
- **Winner and loser are never stored.** They are derived from `outcome`
  at replay, which keeps draws and no-contests representable without
  inventing a winner.
- **`method_cat` is derived, not stored.** Raw `method` stays the single
  source of truth; the decision-versus-finish split is computed in the
  runner.
- **`modelable` is stored but derived.** It is a pure function of
  `outcome` and `method`, so a future change to the exclusion rules
  re-derives it rather than orphaning the ledger.
- **Weight class is the bout's, not the fighter's.** Each bout happened at one weight; a fighter's division is derived at rank time as the modal (or recent) weight class across their bouts, never stored on the fighter.
- **Nothing flavour-specific appears in the ledger.** A control-weighted
  profile or the excitement index reads these same rows with no schema
  change.

### Exclusion and method handling (frozen-project rules)

- Decisions (Unanimous, Split, Majority) take the within-fight dominance
  modifier.
- Finishes (KO/TKO, Submission, Doctor's Stoppage) take the finish
  modifier.
- DQ: standard win with a flag.
- Excluded from modelling: Overturned, Could Not Continue, Other, and No
  Contests. Draws get partial credit (0.5 each).
- These rules define `modelable` and are applied by the runner, not by
  pre-filtering the ledger.

## 6. Continuous-S scoring (what the runner computes)

The score consumes exactly four raw signals per bout, plus the method
baseline. The anchor profile (`dominance_anchors.yaml`) is the source of
truth for the weights; the mechanic is summarised here for portability.

For each bout: look up a method baseline (KO/TKO and submission highest,
split decision lowest), then add a dominance swing capped at a small
maximum, built from four signals scaled to saturation thresholds and
weighted:

| signal | weight | source columns |
|---|---|---|
| significant-strike differential | 0.40 | `sig_strikes_landed_1/_2` |
| control-time differential | 0.30 | `control_seconds_1/_2` |
| knockdown differential | 0.20 | `knockdowns_1/_2` |
| finish round | 0.10 | `finish_round` (zeroed for decisions) |

The weighted sum is scaled by the maximum swing, added to the baseline,
and clipped to the winner range. The loser's score is one minus the
winner's score.

**Binary fallback.** A bout scores continuously when its method is known
and its stat fields are present (`stats_coverage` true). Otherwise it
falls back to a plain binary score (1 / 0, or 0.5 for a draw). In the
frozen data this fallback affected only ~1% of fighter-bouts (draws plus
a handful of pre-stats-era bouts). The dominance modifier is therefore a
bout-level mechanism applied to essentially the whole modelable set; it
is not gated to any fighter subset.

## 7. Replay, idempotency, and provenance

- **Idempotency.** The only mutation anywhere is appending to the ledger.
  Append a bout only if its `bout_id` is not already present. Replaying an
  immutable ledger is deterministic, so re-running the pipeline is always
  safe.
- **Stable sort.** Sort by (`event_date`, `bout_id`) so the history is
  bit-reproducible run to run. Within a single card, order is immaterial:
  no fighter appears twice on one date, and each bout updates from a
  shared pre-fight snapshot.
- **Display-time decay.** Inactivity RD growth is applied lazily, only to
  fighters about to compete, based on each one's own elapsed days since
  their last fight (a 180-day period unit; RD grows, rating does not).
  Stored state is therefore mutated only by real bouts. For a live ranked
  view, project each fighter's RD forward to the snapshot date at read
  time using the same decay function, and discard it after rendering.
  Never write display-time decay back into state, or a returning
  fighter's layoff gets counted twice.
- **Provenance stamp.** Each history file and displayed ranking carries a
  run-meta stamp: ledger version (row count and max event date identify
  it), anchor profile and its hash, engine version, run timestamp. This
  replaces the frozen project's freeze-relative provenance; the live
  statement is "current as of event X, date Y".

## 8. Open and deferred items

- `bout_order` is carried speculatively for later use; nothing reads it
  yet.
- Wide versus long: the eight `_1`/`_2` stat pairs make a wide row. This
  is intentional, to keep a single-file single-read runner. A long-format
  companion (one row per fighter-bout) is tidier on paper but worse to
  work with here; revisit only if the wide row becomes unwieldy.
- Flavours beyond performance: control-weighted is a pure anchor-profile
  swap; the excitement index is a separate per-fight object (validated
  against bonus wins, not trained on them), not a Glicko flavour.
- Automation (event-driven trigger) is deliberately last, after the
  by-hand update is proven and boring.
