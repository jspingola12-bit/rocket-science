import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import rocket_science as rs

CT = ZoneInfo("America/Chicago")
st.set_page_config(page_title="Rocket Science", page_icon="🚀", layout="wide")

st.title("🚀 Rocket Science")
st.caption("Fade the public on NFL & NBA spreads and moneylines when 70%+ of bets or money "
           "is on one side, graded by whether the money backs the tickets up.")

COLS = ["tier_label", "league", "local_time", "matchup", "market", "line", "fade_pick", "odds",
        "public_side", "public_bets_%", "public_money_%", "money_minus_bets", "status", "score", "result"]


def localize(df):
    if df.empty:
        return df
    df = df.copy()
    df["local_time"] = (pd.to_datetime(df["start"], utc=True).dt.tz_convert(CT)
                        .dt.strftime("%a %-m/%-d %-I:%M %p"))
    return df


@st.cache_data(ttl=300, show_spinner="Pulling live splits…")
def load_board(day: dt.date, nfl_on: bool, nba_on: bool):
    games = []
    if nba_on:
        games += rs.fetch_nba(day)
    if nfl_on:
        games += rs.fetch_nfl()
    return pd.DataFrame(rs.board(games))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_history(start: dt.date, end: dt.date, leagues: tuple):
    bar = st.progress(0.0, text="Pulling history from Action Network…")
    rows = rs.history(leagues=leagues, start=start, end=end, progress=lambda p: bar.progress(p))
    bar.empty()
    return pd.DataFrame(rows)


tab_board, tab_track, tab_rules = st.tabs(["Today's board", "Track record / backtest", "How it works"])

with tab_board:
    c1, c2, c3 = st.columns([1.2, 1, 1])
    day = c1.date_input("Date", dt.datetime.now(CT).date())
    nfl_on = c2.toggle("NFL", True)
    nba_on = c3.toggle("NBA", True)
    c4, c5 = st.columns([1.2, 2])
    nfl_scope = c4.radio("NFL games", ["Whole week", "This date only"], horizontal=True)
    plays_only = c5.toggle("Plays only (Liftoff / Orbit)", False)
    if st.button("🔄 Refresh splits"):
        load_board.clear()
    try:
        df = localize(load_board(day, nfl_on, nba_on))
    except Exception as e:
        st.error(f"Couldn't reach Action Network: {e}")
        df = pd.DataFrame()

    if not df.empty and nfl_scope == "This date only":
        local_day = pd.to_datetime(df["start"], utc=True).dt.tz_convert(CT).dt.date
        df = df[(df.league != "NFL") | (local_day == day)]

    if df.empty:
        st.info("No games on the board for this date.")
    else:
        live = df[df.status != "complete"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Liftoff plays", int((live.tier == "LIFTOFF").sum()))
        m2.metric("Orbit leans", int((live.tier == "ORBIT").sum()))
        m3.metric("Games", int(df.game_id.nunique()))
        view = df[df.tier.isin(["LIFTOFF", "ORBIT"])] if plays_only else df
        st.dataframe(view[[c for c in COLS if c in view]], hide_index=True, width="stretch", height=600,
                     column_config={"tier_label": "Tier", "fade_pick": "Fade pick", "line": "Line",
                                    "public_side": "Most-bet side", "local_time": "Time (CT)",
                                    "public_bets_%": st.column_config.NumberColumn("Bets %", format="%d%%"),
                                    "public_money_%": st.column_config.NumberColumn("Money %", format="%d%%"),
                                    "money_minus_bets": st.column_config.NumberColumn("Money − bets", format="%+d"),
                                    "odds": st.column_config.NumberColumn("Odds", format="%+d")})
        st.caption("🚀 Liftoff = bet 1u · 🛰️ Orbit = lean 0.5u · 🔭 Watch = 70% side but no edge · "
                   "⛔ Abort = public money agrees, don't fade · 🌍 Grounded = no side at 70%. "
                   "Splits move until game time, so check close to kickoff or tip.")

with tab_track:
    st.write("Rebuilds every graded fade straight from Action Network's history "
             "(splits data starts fall 2023). The full range takes a few minutes the first time, then it's cached.")
    c1, c2, c3 = st.columns(3)
    start = c1.date_input("From", rs.SPLITS_START, min_value=rs.SPLITS_START)
    end = c2.date_input("To", dt.datetime.now(CT).date() - dt.timedelta(days=1))
    leagues = c3.multiselect("Leagues", ["nfl", "nba"], ["nfl", "nba"])
    if st.button("Run", type="primary") and leagues:
        st.session_state["hist"] = load_history(start, end, tuple(leagues))
    hist = st.session_state.get("hist")
    if hist is not None and not hist.empty:
        rows = hist.to_dict("records")
        st.subheader("By tier")
        st.dataframe(pd.DataFrame(rs.summarize(rows, by=("league", "market", "tier"))), hide_index=True,
                     width="stretch")
        plays = hist[hist.tier.isin(["LIFTOFF", "ORBIT"])].copy()
        if not plays.empty:
            plays["staked"] = plays["profit_1u"] * plays["units"]
            plays = plays.sort_values("start")
            plays["bankroll_units"] = plays["staked"].cumsum()
            st.subheader("Rocket Science plays, units won (tier-sized stakes)")
            st.line_chart(plays.set_index(pd.to_datetime(plays["start"]))["bankroll_units"])
            st.subheader("By season")
            st.dataframe(pd.DataFrame(rs.summarize(plays.to_dict("records"),
                                                   by=("league", "market", "tier", "season"))),
                         hide_index=True, width="stretch")
        st.download_button("Download all graded rows (CSV)", hist.to_csv(index=False), "rocket_science_history.csv")

with tab_rules:
    st.markdown("""
**Public side** = any side with **≥70% of bets or ≥70% of money** (Action Network consensus).
Rocket Science then grades the fade by comparing the public's **money %** to its **bets %**:

| Tier | Rule | Stake | Backtest (fall 2023 – Oct 2026, closing splits) |
|---|---|---|---|
| 🚀 Liftoff | NFL spread · public ≥70% of bets · public money % **lower** than bets % | 1u | 153 bets, 89-63, **+11.4% ROI** |
| 🛰️ Orbit | NFL moneyline · same rule | 0.5u | 364 bets, **+3.8% ROI** |
| 🛰️ Orbit | NBA spread · public ≥70% of bets · money 1–9 pts lower than bets | 0.5u | 471 bets, 249-215, **+2.6% ROI** |
| 🔭 Watch | Other ≥70% bets with lighter money (e.g. NBA ML) | 0 | Negative or break-even |
| ⛔ Abort | Public money ≥ public bets, or ≥70% money on <70% bets | 0 | **−5% to −27% ROI**. Big money is *with* the public |
| 🌍 Grounded | No side has 70% of bets or money | 0 | Not a fade spot. Shown so every game is on the board |

Blind-fading every 70% side: NFL spread +2.2%, NBA spread −5.8%, NFL ML −7.8%, NBA ML negative.

**Caveats:** about 3 seasons of data; NFL samples are small (150-ish bets), so expect variance.
Results assume flat stakes at the consensus closing price. Sportsbooks' own splits differ from Action Network's consensus.
""")
