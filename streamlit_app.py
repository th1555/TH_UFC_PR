"""
streamlit_app.py — TH_UFC_PR exploration dashboard.

A read-and-recompute viewer over the ranking functions. Two modes:
  - Power ranking: the ordered board (P4P male/female, or a division current
    board), with the ranking dials exposed.
  - Momentum: who is hottest over a fixed recent window, sorted by form.
It loads the committed ledger and recomputes; it never writes or commits. A
"fetch latest" button previews new events in memory without saving them.

Run locally:  streamlit run streamlit_app.py
Hosted:       Streamlit Community Cloud, main file streamlit_app.py.
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
# Data: load the committed ledger, apply aliases, rebuild once. Cached on the
# ledger file's fingerprint so it only recomputes when the file changes.
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
    """Return (ledger, pooled_current, {division: current}, momentum, history)."""
    anchors = load_anchors(ANCHORS)
    history, pooled = runner.rebuild(ledger, anchors)
    mom = rankings.momentum(history)
    div_currents = {d: rankings.division_current(ledger, anchors, d)
                    for d in rankings.REAL_DIVISIONS}
    return ledger, pooled, div_currents, mom, history


def get_data():
    """Committed data, unless a live-feed preview is active this session."""
    if "preview" in st.session_state:
        return st.session_state["preview"]["data"]
    return _rebuild_from_committed(_ledger_fingerprint())


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("View")
mode = st.sidebar.radio("Mode", ["Power ranking", "Momentum (who's hot)"])
view = st.sidebar.selectbox(
    "Ranking",
    ["Pound-for-pound (Male)", "Pound-for-pound (Female)"] + rankings.REAL_DIVISIONS,
)
top_n = st.sidebar.slider("Show top", 5, 50, 20, 5)

if mode == "Power ranking":
    st.sidebar.header("Dials")
    st.sidebar.caption("How the ordering responds to uncertainty and inactivity.")
    k = st.sidebar.slider("Uncertainty penalty (k)", 0.0, 3.0, rankings.DEFAULT_K, 0.1)
    floor = st.sidebar.slider("Inactivity floor", 0.18, 0.80,
                              rankings.DEFAULT_INACTIVITY_FLOOR, 0.01)
    gate = st.sidebar.slider("Activity gate (months)", 6, 36,
                             rankings.DEFAULT_ACTIVE_MONTHS, 1)
else:
    st.sidebar.header("Momentum window")
    st.sidebar.caption("Who's built the most form over a fixed recent window.")
    window = st.sidebar.slider("Window (months)", 6, 36, 24, 3)
    min_fights = st.sidebar.slider("Minimum fights in window", 2, 6, 3, 1)

st.sidebar.header("Live feed")
if st.sidebar.button("Fetch latest events (preview, not saved)"):
    committed_ledger = pd.read_parquet(LEDGER)
    with st.spinner("Fetching the feed and folding new events..."):
        new_ledger, status = ingest.ingest(committed_ledger)
        aliases = yaml.safe_load(ALIASES.read_text()) if ALIASES.exists() else {}
        new_ledger = ingest.apply_aliases(new_ledger, aliases or {})
        st.session_state["preview"] = {"data": _rebuild_all(new_ledger), "status": status}
if "preview" in st.session_state and st.sidebar.button("Clear preview (back to committed)"):
    del st.session_state["preview"]


# ---------------------------------------------------------------------------
# Build the selected board
# ---------------------------------------------------------------------------
ledger, pooled, div_currents, mom, history = get_data()
ref = pd.Timestamp.today().normalize()
is_p4p = view.startswith("Pound-for-pound")
sex = ("M" if "Male" in view else "F") if is_p4p else None

if mode == "Power ranking":
    dial = dict(ref_date=ref, k=k, inactivity_floor=floor, active_months=gate, top=top_n)
    if is_p4p:
        board = rankings.pound_for_pound(pooled, ledger, sex=sex, **dial)
    else:
        board = rankings.rank(div_currents[view], ledger[ledger["weightclass"] == view], **dial)
    board = board.merge(mom[["FIGHTER", "FORM_SCORE", "FORM"]], on="FIGHTER", how="left")
    cols = {
        "RANK": "#", "FIGHTER": "Fighter", "RATING": "Rating",
        "RD_NOW": "RD", "CR": "Conservative", "FORM_SCORE": "Form", "FORM": "Last 5",
        "MONTHS_SINCE_SEEN": "Months idle", "TIER_NOW": "Confidence", "N_FIGHTS": "UFC fights",
    }
    fmt = {"Rating": "{:.0f}", "RD": "{:.0f}", "Conservative": "{:.0f}",
           "Form": "{:+.0f}", "Months idle": "{:.1f}"}
else:
    board = rankings.hot_list(history, ledger, ref_date=ref, window_months=window,
                              min_fights=min_fights,
                              division=None if is_p4p else view, sex=sex, top=top_n)
    cols = {"RANK": "#", "FIGHTER": "Fighter", "FORM_SCORE": "Form",
            "FORM": f"Record ({window}mo)", "N_RECENT": "Fights"}
    fmt = {"Form": "{:+.0f}"}


# ---------------------------------------------------------------------------
# Header + status
# ---------------------------------------------------------------------------
as_of = pd.to_datetime(ledger["event_date"]).max().date()
n_fighters = len(set(ledger["fighter_1"]) | set(ledger["fighter_2"]))
st.title("UFC Power Rankings")
label = f"{view}" + ("  ·  momentum" if mode != "Power ranking" else "")
st.caption(f"{label}  ·  data through {as_of}  ·  {n_fighters:,} fighters  ·  "
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
show = board[[c for c in cols if c in board.columns]].rename(columns=cols)
st.dataframe(
    show.style.format(fmt),
    hide_index=True, width='stretch', height=min(38 * (top_n + 1), 900),
)

with st.expander("What the columns mean"):
    if mode == "Power ranking":
        st.markdown(
            "- **Rating**: Glicko-2 strength; higher is stronger. Never decays "
            "for inactivity.\n"
            "- **RD**: uncertainty as of today; grows with a layoff and with too "
            "few fights.\n"
            "- **Conservative**: Rating minus (k x RD), the lower edge of what "
            "we're confident of. The list sorts by this, so the uncertain are "
            "demoted.\n"
            "- **Form**: recent form over the last 5 fights, quality-adjusted "
            "(how much they beat what the ratings predicted). Positive is hot.\n"
            "- **Last 5**: win-loss-draw string for those fights.\n"
            "- **Months idle**: time since the last appearance (a No Contest "
            "counts).\n"
            "- **Confidence**: how well the record establishes the rating, from "
            "the settled uncertainty before any inactivity adjustment."
        )
    else:
        st.markdown(
            "- **Form**: average per-fight surprise (how much they beat what the "
            "ratings predicted) over the window, scaled. Opponent quality is "
            "built in: beating a stronger opponent counts far more. Positive is "
            "hot, negative cold.\n"
            f"- **Record ({window}mo)** / **Fights**: win-loss string and count "
            "of fights inside the window. A fighter needs the minimum number of "
            "fights to qualify, which keeps small samples off the list."
        )

# --- seam for week-over-week movement (deferred) ---------------------------
# Save the current board dated, and a future version joins last week's on
# FIGHTER to add a rank-change column.
