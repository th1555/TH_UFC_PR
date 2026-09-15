"""
streamlit_app.py — TH_UFC_PR exploration dashboard.

A read-and-recompute viewer over the ranking functions. It loads the committed
ledger, rebuilds ratings, and lets you explore the pound-for-pound lists
(male / female) and the per-division current boards, with the ranking dials
exposed so you can see how the ordering responds. It never writes or commits;
data updates happen via update.py run separately. A "fetch latest" button can
PREVIEW new events in-memory without saving them.

Run locally:  streamlit run streamlit_app.py
Hosted:       Streamlit Community Cloud, pointed at this repo (main file
              streamlit_app.py).
"""
from pathlib import Path
import pandas as pd
import yaml
import streamlit as st

import ingest
import runner
import rankings
from scoring import load_anchors

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "data" / "bout_ledger.parquet"
ANCHORS = ROOT / "anchors" / "performance.yaml"
ALIASES = ROOT / "name_aliases.yaml"

st.set_page_config(page_title="UFC Power Rankings", layout="wide")


# ---------------------------------------------------------------------------
# Data: load the committed ledger, apply aliases, rebuild ratings once. Cached
# on the ledger file's fingerprint so it only recomputes when the file changes.
# ---------------------------------------------------------------------------
def _ledger_fingerprint():
    s = LEDGER.stat()
    return (s.st_mtime, s.st_size)


@st.cache_data(show_spinner="Rebuilding ratings from the ledger...")
def _rebuild_from_committed(_fingerprint):
    ledger = pd.read_parquet(LEDGER)
    aliases = yaml.safe_load(ALIASES.read_text()) if ALIASES.exists() else {}
    ledger = ingest.apply_aliases(ledger, aliases or {})
    return _rebuild_all(ledger)


def _rebuild_all(ledger):
    """Return (ledger, pooled_current, {division: division_current}, momentum)."""
    anchors = load_anchors(ANCHORS)
    history, pooled = runner.rebuild(ledger, anchors)
    mom = rankings.momentum(history)
    div_currents = {d: rankings.division_current(ledger, anchors, d)
                    for d in rankings.REAL_DIVISIONS}
    return ledger, pooled, div_currents, mom


def get_data():
    """Committed data, unless a live-feed preview is active this session."""
    if "preview" in st.session_state:
        return st.session_state["preview"]["data"]
    return _rebuild_from_committed(_ledger_fingerprint())


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
st.sidebar.header("View")
DIVISIONS = rankings.REAL_DIVISIONS
view = st.sidebar.selectbox(
    "Ranking",
    ["Pound-for-pound (Male)", "Pound-for-pound (Female)"] + DIVISIONS,
)
top_n = st.sidebar.slider("Show top", 5, 50, 20, 5)

st.sidebar.header("Dials")
st.sidebar.caption("How the ordering responds to uncertainty and inactivity.")
k = st.sidebar.slider("Uncertainty penalty (k)", 0.0, 3.0, rankings.DEFAULT_K, 0.1)
floor = st.sidebar.slider("Inactivity floor", 0.18, 0.80,
                          rankings.DEFAULT_INACTIVITY_FLOOR, 0.01)
gate = st.sidebar.slider("Activity gate (months)", 6, 36,
                         rankings.DEFAULT_ACTIVE_MONTHS, 1)

st.sidebar.header("Live feed")
if st.sidebar.button("Fetch latest events (preview, not saved)"):
    committed_ledger = pd.read_parquet(LEDGER)
    with st.spinner("Fetching the feed and folding new events..."):
        new_ledger, status = ingest.ingest(committed_ledger)
        aliases = yaml.safe_load(ALIASES.read_text()) if ALIASES.exists() else {}
        new_ledger = ingest.apply_aliases(new_ledger, aliases or {})
        st.session_state["preview"] = {"data": _rebuild_all(new_ledger), "status": status}
if "preview" in st.session_state:
    if st.sidebar.button("Clear preview (back to committed)"):
        del st.session_state["preview"]


# ---------------------------------------------------------------------------
# Build the selected board
# ---------------------------------------------------------------------------
ledger, pooled, div_currents, mom = get_data()
ref = pd.Timestamp.today().normalize()
dial = dict(ref_date=ref, k=k, inactivity_floor=floor, active_months=gate, top=top_n)

if view.startswith("Pound-for-pound"):
    sex = "M" if "Male" in view else "F"
    board = rankings.pound_for_pound(pooled, ledger, sex=sex, **dial)
else:
    sub = ledger[ledger["weightclass"] == view]
    board = rankings.rank(div_currents[view], sub, **dial)

board = board.merge(mom[["FIGHTER", "FORM_SCORE", "FORM"]], on="FIGHTER", how="left")


# ---------------------------------------------------------------------------
# Header + status
# ---------------------------------------------------------------------------
as_of = pd.to_datetime(ledger["event_date"]).max().date()
n_fighters = len(set(ledger["fighter_1"]) | set(ledger["fighter_2"]))
st.title("UFC Power Rankings")
st.caption(f"{view}  ·  data through {as_of}  ·  {n_fighters:,} fighters  ·  "
           f"rating = UFC-only Glicko-2 with a continuous-S dominance modifier")

if "preview" in st.session_state:
    s = st.session_state["preview"]["status"]
    st.info(f"Previewing the live feed: {s['new_bouts']} new bouts across "
            f"{s['new_events']} events folded in memory (not saved to the repo). "
            f"Feed latest {pd.to_datetime(s['feed_latest']).date()}.")
    if s.get("name_warnings"):
        st.warning(f"Name collisions to review: {list(s['name_warnings'].values())}")


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
cols = {
    "RANK": "#", "FIGHTER": "Fighter", "RATING": "Rating",
    "RD_NOW": "RD", "CR": "Conservative", "FORM_SCORE": "Form", "FORM": "Last 5",
    "MONTHS_SINCE_SEEN": "Months idle", "TIER_NOW": "Confidence", "N_FIGHTS": "UFC fights",
}
show = board[[c for c in cols if c in board.columns]].rename(columns=cols)
st.dataframe(
    show.style.format({"Rating": "{:.0f}", "RD": "{:.0f}",
                       "Conservative": "{:.0f}", "Months idle": "{:.1f}"}),
    hide_index=True, width='stretch', height=min(38 * (top_n + 1), 900),
)

with st.expander("What the columns mean"):
    st.markdown(
        "- **Rating**: the fighter's Glicko-2 strength; higher is stronger. "
        "Never decays for inactivity.\n"
        "- **RD**: uncertainty in that rating as of today; grows with a layoff "
        "and with too few fights.\n"
        "- **Conservative**: Rating minus (k x RD), the lower edge of what "
        "we're confident of. This is what the list is sorted by, so the "
        "uncertain are demoted.\n"
        "- **Months idle**: time since the fighter's last appearance (a No "
        "Contest counts as an appearance).\n"
        "- **Confidence**: how well the fighter's record establishes the rating "
        "(Established / Provisional / Unreliable), based on their settled "
        "uncertainty before any inactivity adjustment."
    )

# --- seam for week-over-week movement (deferred) ---------------------------
# When enabled: load last week's saved board, join on FIGHTER, and add a
# movement column (rank delta). Nothing here yet; the snapshot step writes a
# dated board that a future version reads and diffs against.
