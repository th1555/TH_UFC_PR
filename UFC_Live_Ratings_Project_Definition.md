# UFC Live Ratings: Project Definition

Short orientation for the post-submission, live-updating extension of the
UFC fighter-value work. Read alongside `UFC_Live_Ratings_Ledger_Schema.md`,
which holds the technical detail this document only summarises.

Status: personal build-out, design stage. Last updated 2026-09-09.

---

## What we are building

The MSc froze the competitive axis (Glicko-2 with a continuous-S
dominance modifier) at UFC 329, 11 July 2026. This turns that frozen axis
into a live system: ratings that recompute after each UFC event so the
ranking is current rather than a snapshot. One engine produces several
ratings ("flavours") by swapping the dominance-weight profile it reads at
replay: a pure-performance rating first, a control-weighted rating
second, and, as a separate object rather than a Glicko flavour, an
excitement rating. Flavours are exposed as filters.

It is a personal extension, not an MSc deliverable; the frozen notebooks,
proposal, and public-profile axis are read-only reference, not a
specification to conform to. The purpose is to keep the competitive
ratings current, to see how re-weighting dominance changes who ranks
where, and to test whether an excitement signal built from fight stats
tracks the UFC's own bonus awards. The consumer at present is Tom's own
use and the existing dashboard; no external user is defined (see Open
questions).

## Rough roadmap

The near-term commitment is phases 0 to 2; the rest is the horizon, each a
natural single-session unit, not a commitment.

0. **Foundations.** Extract the Glicko engine from notebook 11 into an
   importable module; define the ledger, history, and anchor artefacts;
   establish the regression gate (a replay from seed to UFC 329 must
   reproduce the frozen ranking within tolerance). Nothing proceeds until
   the gate passes.
1. **Live performance rating.** Run the per-event update by hand as a
   script. Backfilling the events between the freeze and now is this
   mechanism's first real exercise, not a warm-up.
2. **Ingestion hardening.** Add a staleness health check on the
   Greco1899 feed, new-event detection, and close the name-resolver for
   new fighters (live events bring in names the loose normaliser would
   mismatch).
3. **Control-weighted flavour.** A pure anchor-profile swap; proves the
   multi-flavour architecture on the cheap sibling.
4. **Excitement index.** A transparent per-fight score from observable
   stats, aggregated per fighter with recency weighting, validated by
   rank-correlation against bonus wins rather than trained on them.
5. **Dashboard integration.** Flavours as filters, the historical "as of
   event X" view wired to the history, display-time decay in the ranked
   view.
6. **Automation.** An event-driven trigger (a scheduled GitHub Action
   fits the setup). Deliberately last, once the by-hand update is boring.
7. **ML excitement (stretch).** Can a learned model beat the transparent
   baseline at predicting held-out bonuses, with label contamination
   named as the known limitation? Only worth doing once the baseline
   exists to beat.

## Technical outline

- **Rebuild-from-seed** from an immutable, append-only bout ledger of raw
  facts. SCORE is computed by the runner at replay from the chosen anchor
  profile, never stored. At this data scale a full replay is seconds.
- **Three artefacts:** the bout ledger (source of truth), one anchor
  profile YAML per flavour, and the rating history (replay output).
  Snapshots are filters on the history, not separate files. Full schema
  in the companion document.
- **Engine:** Glicko-2 with the continuous-S dominance modifier.
  Inactivity grows RD lazily (a 180-day period unit) and leaves the
  rating itself unchanged; a live ranked view projects RD forward to the
  snapshot date at read time (display-time decay) and never writes it
  back.
- **Data:** Greco1899's daily auto-refresh is the primary live feed, with
  a staleness check asserting the latest event date has advanced; Tom's
  own scraper is the manual fallback; Fight Matrix stays a validation
  target, not an ingestion source.
- **Reproducibility (binding constraint):** each post-event update is a
  single, simple, deterministic step. Replay is bit-reproducible via a
  stable sort; idempotency is a `bout_id` membership check on append;
  provenance is a per-run stamp reading "current as of event X, date Y".
- **Stack:** Surface Studio Pro plus Colab; GitHub for milestone
  snapshots.

## Success tests

- **Performance rating:** reproduces the frozen ranking at UFC 329, and
  predicts future fight outcomes better than a sensible baseline.
- **Control flavour:** produces explicable divergence from the
  performance rating, not noise.
- **Excitement index:** shows positive rank-correlation with bonus
  (Fight/Performance of the Night) wins on events it was not fitted to.

## Scope boundaries (not this)

- No computer vision and no heavy deep learning, unless explicitly
  reopened.
- No new public-profile axis; that axis stays as delivered in the MSc.
  This build-out is the competitive axis plus the excitement object.
- Excitement is never trained on bonuses; bonuses are the validation
  target only. The index proxies how war-like a fight was; it cannot
  measure the round-to-round swing (no scorecard data), and that limit is
  stated up front.

## Open questions

- Downstream consumer beyond Tom's own use and the dashboard: undefined.
- Excitement feature set and weights: to be decided.
- Whether an excitement or current-form flavour should decay the rating
  itself, not only RD, during inactivity: deferred.
