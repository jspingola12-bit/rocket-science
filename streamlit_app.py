import datetime as dt
import html
import json
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_js_eval import streamlit_js_eval

import rocket_science as rs

CT = ZoneInfo("America/Chicago")
APP_VERSION = 3  # bump to bust cached boards after a model change
STORE_KEY = "rocket_science_bets_v1"
TIER_COLOR = {"LIFTOFF": "#ff7a3d", "ORBIT": "#4da3ff"}

st.set_page_config(page_title="Rocket Science", page_icon="🚀", layout="wide")
st.markdown("""<style>
.rs-card{border:2px solid var(--c);border-radius:16px;padding:14px 18px 10px;margin:6px 0 4px;
         background:linear-gradient(135deg,color-mix(in srgb,var(--c) 14%,transparent),transparent)}
.rs-tier{font-size:.8rem;font-weight:800;letter-spacing:.08em;color:var(--c);text-transform:uppercase}
.rs-pick{font-size:2.4rem;font-weight:900;line-height:1.05;margin:4px 0 2px}
.rs-odds{font-size:1.25rem;font-weight:700;opacity:.8;margin-left:.35rem}
.rs-meta{font-size:.9rem;opacity:.75;margin-top:4px}
.rs-total{font-size:3.2rem;font-weight:900;line-height:1}
.rs-sub{font-size:.95rem;opacity:.75}
</style>""", unsafe_allow_html=True)

st.title("🚀 Rocket Science")
st.caption("Fading the public should not be rocket science! Created by James Spingola")

COLS = ["tier_label", "league", "local_time", "matchup", "market", "line", "fade_pick", "odds",
        "public_side", "public_bets_%", "public_money_%", "money_minus_bets", "status", "score", "result"]


def local_time(iso: str) -> str:
    return pd.Timestamp(iso).tz_convert(CT).strftime("%a %-m/%-d %-I:%M %p")


def localize(df):
    if df.empty:
        return df
    df = df.copy()
    df["local_time"] = [local_time(s) for s in df["start"]]
    return df


@st.cache_data(ttl=300, show_spinner="Pulling live splits…")
def load_board(day: dt.date, nfl_on: bool, nba_on: bool, _v: int = APP_VERSION):
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


@st.cache_data(ttl=600, show_spinner=False)
def final_game(league, game_id, start, season, week):
    g = rs.find_game({"league": league, "game_id": game_id, "start": start, "season": season, "week": week})
    return None if g is None else rs.asdict(g)


# ---------------------------------------------------------------- my bets (saved in this browser)
def load_bets():
    if "bets" in st.session_state:
        return
    raw = streamlit_js_eval(js_expressions=f"localStorage.getItem('{STORE_KEY}') || '[]'", key="ls_get")
    if raw is None:
        return  # browser hasn't answered yet; Streamlit reruns when it does
    try:
        st.session_state.bets = json.loads(raw)
    except Exception:
        st.session_state.bets = []


def persist_bets():
    if "bets" in st.session_state and st.session_state.get("dirty"):
        payload = json.dumps(st.session_state.bets)
        streamlit_js_eval(js_expressions=f"localStorage.setItem('{STORE_KEY}', {json.dumps(payload)}); 'saved'",
                          key="ls_set")


def bets_ready() -> bool:
    return "bets" in st.session_state


def add_bet(row: dict, market: str, side: str, units: float, source: str):
    team, line, odds = rs.side_quote(row, market, side)
    if odds is None or (market == "spread" and line is None):
        st.toast("No line posted for that side yet.")
        return
    bid = f'{row["game_id"]}-{market}-{side}'
    if any(b["id"] == bid for b in st.session_state.bets):
        st.toast("Already tracking that bet.")
        return
    st.session_state.bets.append({
        "id": bid, "league": row["league"], "game_id": int(row["game_id"]), "start": row["start"],
        "season": None if pd.isna(row.get("season")) else int(row["season"]),
        "week": None if pd.isna(row.get("week")) else int(row["week"]),
        "matchup": row["matchup"], "market": market, "side": side, "team": team,
        "line": line, "odds": float(odds), "units": float(units), "pick": rs.pick_label(team, market, line),
        "source": source, "added": dt.datetime.now(CT).isoformat(timespec="minutes"),
        "result": None, "units_won": None,
    })
    st.session_state.dirty = True
    st.toast(f"Tracking {rs.pick_label(team, market, line)} for {units:g}u")


def grade_pending():
    now = pd.Timestamp.now(tz="UTC")
    changed = False
    for b in st.session_state.bets:
        if b["result"] is not None or pd.Timestamp(b["start"]) > now:
            continue
        g = final_game(b["league"], b["game_id"], b["start"], b.get("season"), b.get("week"))
        if not g or g["status"] != "complete":
            continue
        b.update(rs.grade_bet(b, rs.Game(**g)))
        changed = changed or b["result"] is not None
    if changed:
        st.session_state.dirty = True


load_bets()


def fmt_odds(o) -> str:
    return "" if o is None or pd.isna(o) else f"{o:+.0f}"


def play_card(r: dict):
    c = TIER_COLOR[r["tier"]]
    stake = rs.TIERS[r["tier"]]["units"]
    action = "Bet" if r["tier"] == "LIFTOFF" else "Lean"
    st.markdown(f"""<div class="rs-card" style="--c:{c}">
<div class="rs-tier">{html.escape(r["tier_label"])} · {action} {stake:g}u · {r["league"]} {r["market"]}</div>
<div class="rs-pick">{html.escape(r["fade_pick"])}<span class="rs-odds">{fmt_odds(r["odds"])}</span></div>
<div class="rs-meta">{html.escape(r["matchup"])} · {r["local_time"]} CT<br>
Fading {html.escape(str(r["public_side"]))}: {r["public_bets_%"]:.0f}% of bets, {r["public_money_%"]:.0f}% of money</div>
</div>""", unsafe_allow_html=True)
    mkt = "spread" if r["market"] == "Spread" else "ml"
    bid = f'{r["game_id"]}-{mkt}-{r["pick_side"]}'
    if not bets_ready():
        return
    if any(b["id"] == bid for b in st.session_state.bets):
        st.button("✅ Tracking", key=f"t_{bid}", disabled=True)
    else:
        with st.popover("➕ Track this bet"):
            u = st.number_input("Units", 0.5, 20.0, float(stake), 0.5, key=f"u_{bid}")
            if st.button("Add to My bets", key=f"a_{bid}", type="primary"):
                add_bet(r, mkt, r["pick_side"], u, r["tier_label"])
                st.rerun()


tab_board, tab_bets, tab_track, tab_rules = st.tabs(
    ["Today's board", "💰 My bets", "Track record / backtest", "How it works"])

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
        live = df[df.status == "scheduled"]
        plays = live[live.tier.isin(["LIFTOFF", "ORBIT"])].sort_values(
            ["tier", "start"], key=lambda s: s.map({"LIFTOFF": 0, "ORBIT": 1}) if s.name == "tier" else s)

        st.subheader("🎯 Bets to take")
        if plays.empty:
            st.info("No Liftoff or Orbit plays right now. Splits move, so check back closer to game time.")
        else:
            cols = st.columns(2)
            for i, r in enumerate(plays.to_dict("records")):
                with cols[i % 2]:
                    play_card(r)

        with st.expander("➕ Track any other bet"):
            if not bets_ready():
                st.caption("Loading your saved bets…")
            elif live.empty:
                st.caption("No upcoming games on this board.")
            else:
                games = live.drop_duplicates("game_id")
                labels = {f'{g["league"]} · {g["matchup"]} · {g["local_time"]}': g
                          for g in games.to_dict("records")}
                pick = labels[st.selectbox("Game", list(labels))]
                mkt_name = st.radio("Market", ["Spread", "Moneyline"], horizontal=True, key="any_mkt")
                mkt = "spread" if mkt_name == "Spread" else "ml"
                opts = {}
                for side in ("away", "home"):
                    team, line, odds = rs.side_quote(pick, mkt, side)
                    if odds is not None and not pd.isna(odds) and (mkt == "ml" or line is not None):
                        opts[f"{rs.pick_label(team, mkt, line)}  ({fmt_odds(odds)})"] = side
                if not opts:
                    st.caption("No line posted for this market yet.")
                else:
                    side = opts[st.radio("Side", list(opts), horizontal=True, key="any_side")]
                    u = st.number_input("Units", 0.5, 20.0, 1.0, 0.5, key="any_units")
                    if st.button("Add to My bets", type="primary", key="any_add"):
                        add_bet(pick, mkt, side, u, "My pick")
                        st.rerun()

        st.subheader("📋 Every game")
        live_ct = df[df.status != "complete"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Liftoff plays", int((live_ct.tier == "LIFTOFF").sum()))
        m2.metric("Orbit leans", int((live_ct.tier == "ORBIT").sum()))
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

with tab_bets:
    if not bets_ready():
        st.info("Loading your saved bets…")
    else:
        grade_pending()
        bets = st.session_state.bets
        done = [b for b in bets if b["result"] is not None]
        pending = [b for b in bets if b["result"] is None]
        total = sum(b["units_won"] for b in done)
        risked = sum(b["units"] for b in done)
        w = sum(b["result"] == "W" for b in done)
        l = sum(b["result"] == "L" for b in done)
        p = sum(b["result"] == "P" for b in done)
        color = "#2ecc71" if total > 0 else "#ff5c5c" if total < 0 else "inherit"
        st.markdown(f"""<div class="rs-total" style="color:{color}">{total:+.2f}u</div>
<div class="rs-sub">Record {w}-{l}-{p} · ROI {(100 * total / risked if risked else 0):+.1f}% · {len(pending)} pending</div>""",
                    unsafe_allow_html=True)

        if done:
            hist = pd.DataFrame(done).sort_values("start")
            hist["Units"] = hist["units_won"].cumsum()
            st.line_chart(hist.set_index(pd.to_datetime(hist["start"]))["Units"], height=220)
            by_src = hist.groupby("source").agg(bets=("id", "count"), units=("units_won", "sum")).reset_index()
            st.dataframe(by_src.rename(columns={"source": "Type"}), hide_index=True)

        st.subheader("⏳ Pending")
        if not pending:
            st.caption("No open bets. Tap ➕ Track this bet on any play, or use ➕ Track any other bet.")
        for b in sorted(pending, key=lambda x: x["start"]):
            a, z = st.columns([5, 1])
            a.markdown(f'**{b["pick"]}** ({fmt_odds(b["odds"])}) · {b["units"]:g}u  \n'
                       f'<span class="rs-sub">{b["league"]} · {b["matchup"]} · {local_time(b["start"])} CT · {b["source"]}</span>',
                       unsafe_allow_html=True)
            if z.button("🗑️", key=f"del_{b['id']}", help="Remove this bet"):
                st.session_state.bets = [x for x in bets if x["id"] != b["id"]]
                st.session_state.dirty = True
                st.rerun()

        st.subheader("✅ Settled")
        if done:
            show = pd.DataFrame(done).sort_values("start", ascending=False)
            show["When"] = [local_time(s) for s in show["start"]]
            show["Odds"] = [fmt_odds(o) for o in show["odds"]]
            st.dataframe(show[["When", "league", "matchup", "pick", "Odds", "units", "result", "units_won", "source"]]
                         .rename(columns={"league": "League", "matchup": "Game", "pick": "Bet", "units": "Stake",
                                          "result": "Result", "units_won": "Units", "source": "Type"}),
                         hide_index=True, width="stretch")
        else:
            st.caption("Bets settle automatically once the game is final.")

        with st.expander("Backup / restore"):
            st.caption("Your bets are saved in this browser on this device. "
                       "Download a backup now and then, and restore it on another device if you switch.")
            st.download_button("⬇️ Download backup", json.dumps(bets, indent=1), "rocket_science_bets.json",
                               mime="application/json")
            up = st.file_uploader("Restore from backup", type="json")
            if up is not None and st.button("Restore (adds any bets you don't already have)"):
                have = {b["id"] for b in bets}
                new = [b for b in json.load(up) if b.get("id") not in have]
                st.session_state.bets = bets + new
                st.session_state.dirty = True
                st.toast(f"Restored {len(new)} bets")
                st.rerun()

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

**My bets** locks in the line and odds at the moment you tap Track, then settles each bet automatically when the game goes final.

**Caveats:** about 3 seasons of data; NFL samples are small (150-ish bets), so expect variance.
Results assume flat stakes at the consensus closing price. Sportsbooks' own splits differ from Action Network's consensus.
""")

persist_bets()
