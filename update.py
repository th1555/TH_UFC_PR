"""
update.py — one-step update: fetch new events, fold them in, rebuild ratings.

This is the by-hand / Colab entry point, and the core a dashboard rerun
button wraps. Compute is pure (ingest + rebuild); persistence (writing the
ledger and outputs) is kept here and separate, since where a hosted rerun
persists is still an open decision.

    python update.py            # fetch, fold, rebuild, write, print status
"""
from pathlib import Path
import pandas as pd

import yaml

import ingest
import runner
from scoring import load_anchors

ROOT = Path(__file__).resolve().parent


def update(write=True, base=ingest.BASE):
    """Fetch, fold new events, rebuild. Returns a status dict for display."""
    ledger_path = ROOT / "data" / "bout_ledger.parquet"
    ledger = pd.read_parquet(ledger_path)

    new_ledger, status = ingest.ingest(ledger, base=base)
    if status["error"]:
        return status

    if write and status["new_bouts"]:
        new_ledger.to_parquet(ledger_path, index=False)   # ledger stays RAW

    # Apply name aliases at rebuild (not baked into the ledger), so a confirmed
    # duplicate merges retroactively. Empty table is a no-op.
    aliases_path = ROOT / "name_aliases.yaml"
    aliases = {}
    if aliases_path.exists():
        aliases = yaml.safe_load(aliases_path.read_text()) or {}
    canon = ingest.apply_aliases(new_ledger, aliases)

    anchors = load_anchors(ROOT / "anchors" / "performance.yaml")
    history, current = runner.rebuild(canon, anchors)

    if write:
        (ROOT / "data").mkdir(exist_ok=True)
        history.to_parquet(ROOT / "data" / "rating_history.parquet", index=False)
        current.to_parquet(ROOT / "data" / "ratings_current.parquet", index=False)

    status["total_bouts"] = int(len(new_ledger))
    status["total_fighters"] = int(len(current))
    return status


if __name__ == "__main__":
    st = update()
    if st.get("error"):
        print("Update failed:", st["error"])
    else:
        print(f"Feed latest: {st['feed_latest']}  |  stale: {st['stale']}")
        print(f"Folded {st['new_bouts']} new bouts across {st['new_events']} events.")
        print(f"Ledger now {st['total_bouts']:,} bouts, {st['total_fighters']:,} fighters.")
