"""
Rocket Science — NFL & NBA public-fade model.

Pulls public betting splits (% of bets / % of money) from Action Network's
public scoreboard API, flags any side where the public has >=70% of bets or
money, and grades the FADE (the other side) into tiers calibrated from a
2023-2026 backtest.

Core idea from the backtest: fading 70% sides blindly does NOT win. The edge
is when the public has 70%+ of the TICKETS but a smaller share of the MONEY
(bigger/sharper bets are on the other side). When public money agrees with
public tickets, fading has lost.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, asdict

import requests

API = "https://api.actionnetwork.com/web/v2/scoreboard/{league}"
CONSENSUS_BOOK = "15"  # Action Network consensus line + public splits
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "application/json",
    "Origin": "https://www.actionnetwork.com",
    "Referer": "https://www.actionnetwork.com/",
}
PUBLIC_THRESHOLD = 70  # % of bets or money that defines "the public side"

# ---------------------------------------------------------------- tiers
# Backtest (closing consensus splits, 2023-24 through 2026 wk 7, 1u flat):
#   NFL spread, tickets>=70 & money<tickets ....... 153 bets 89-63  +11.4% ROI
#   NFL ML,     tickets>=70 & money<tickets ....... 364 bets         +3.8% ROI
#   NBA spread, tickets>=70 & money 1-9 pts lower . 471 bets 249-215 +2.6% ROI
#   Any, public money >= public tickets ........... -5% to -27% ROI  (don't fade)
#   Any, money>=70 but tickets<70 ................. -3% to -15% ROI  (don't fade)
TIERS = {
    "LIFTOFF": {"units": 1.0, "label": "🚀 Liftoff", "desc": "Tier 1 play"},
    "ORBIT": {"units": 0.5, "label": "🛰️ Orbit", "desc": "Tier 2 lean, small stake"},
    "WATCH": {"units": 0.0, "label": "🔭 Watch", "desc": "Public side flagged, no historical edge"},
    "ABORT": {"units": 0.0, "label": "⛔ Abort", "desc": "Public money agrees, fading has lost"},
    "GROUNDED": {"units": 0.0, "label": "🌍 Grounded", "desc": "No side has 70% of bets or money"},
}


def classify(league: str, market: str, pub_tickets: float, pub_money: float) -> str:
    """Tier for fading the public side, given the public side's bet% and money%."""
    div = pub_money - pub_tickets  # negative = money lighter than tickets
    if pub_tickets >= PUBLIC_THRESHOLD and div < 0:
        if league == "nfl" and market == "spread":
            return "LIFTOFF"
        if league == "nfl" and market == "ml":
            return "ORBIT"
        if league == "nba" and market == "spread" and div > -10:
            return "ORBIT"
        return "WATCH"
    return "ABORT"


# ---------------------------------------------------------------- data
@dataclass
class Game:
    league: str
    game_id: int
    start: str
    season: int | None
    week: int | None
    status: str
    away: str
    home: str
    away_score: float | None
    home_score: float | None
    away_spread: float | None
    away_spread_odds: float | None
    home_spread_odds: float | None
    spread_away_tickets: float | None
    spread_away_money: float | None
    away_ml: float | None
    home_ml: float | None
    ml_away_tickets: float | None
    ml_away_money: float | None


def _get(league: str, params: dict, retries: int = 3) -> dict:
    p = {"bookIds": CONSENSUS_BOOK, "periods": "event", **params}
    for i in range(retries):
        try:
            r = requests.get(API.format(league=league), params=p, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return {}


def parse_games(league: str, payload: dict) -> list[Game]:
    out = []
    for g in payload.get("games", []) or []:
        teams = {t["id"]: t.get("abbr") for t in g.get("teams", [])}
        ev = ((g.get("markets") or {}).get(CONSENSUS_BOOK) or {}).get("event") or {}
        aid, hid = g.get("away_team_id"), g.get("home_team_id")

        def side(mkt, tid):
            return next((o for o in ev.get(mkt, []) or [] if o.get("team_id") == tid), {})

        def pct(o, k):
            v = ((o.get("bet_info") or {}).get(k) or {}).get("percent")
            return float(v) if v is not None else None

        sa, sh = side("spread", aid), side("spread", hid)
        ma, mh = side("moneyline", aid), side("moneyline", hid)
        box = g.get("boxscore") or {}
        out.append(Game(
            league=league, game_id=g.get("id"), start=g.get("start_time"),
            season=g.get("season"), week=g.get("week"), status=g.get("status"),
            away=teams.get(aid), home=teams.get(hid),
            away_score=box.get("total_away_points"), home_score=box.get("total_home_points"),
            away_spread=sa.get("value"), away_spread_odds=sa.get("odds"), home_spread_odds=sh.get("odds"),
            spread_away_tickets=pct(sa, "tickets"), spread_away_money=pct(sa, "money"),
            away_ml=ma.get("odds"), home_ml=mh.get("odds"),
            ml_away_tickets=pct(ma, "tickets"), ml_away_money=pct(ma, "money"),
        ))
    return out


def fetch_nba(date: dt.date) -> list[Game]:
    return parse_games("nba", _get("nba", {"date": date.strftime("%Y%m%d")}))


def fetch_nfl(season: int | None = None, week: int | None = None, season_type: str = "reg") -> list[Game]:
    params = {} if season is None else {"season": season, "week": week, "seasonType": season_type}
    return parse_games("nfl", _get("nfl", params))


# ---------------------------------------------------------------- signals
def _payout(odds: float) -> float:
    return odds / 100 if odds > 0 else 100 / abs(odds)


def signals(game: Game) -> list[dict]:
    """One row per market where a side has >=70% of bets or money."""
    rows = []
    mkts = {
        "spread": (game.spread_away_tickets, game.spread_away_money),
        "ml": (game.ml_away_tickets, game.ml_away_money),
    }
    for mkt, (at, am) in mkts.items():
        if at is None or am is None or (at == 0 and am == 0):
            continue
        ht, hm = 100 - at, 100 - am
        a_hot = at >= PUBLIC_THRESHOLD or am >= PUBLIC_THRESHOLD
        h_hot = ht >= PUBLIC_THRESHOLD or hm >= PUBLIC_THRESHOLD
        if a_hot == h_hot:  # neither side, or tickets/money split across sides
            continue
        pub = "away" if a_hot else "home"
        pt, pm = (at, am) if pub == "away" else (ht, hm)
        fade = "home" if pub == "away" else "away"
        fade_team = game.home if fade == "home" else game.away
        pub_team = game.away if pub == "away" else game.home

        if mkt == "spread":
            if game.away_spread is None:
                continue
            line = game.away_spread if fade == "away" else -game.away_spread
            odds = (game.away_spread_odds if fade == "away" else game.home_spread_odds) or -110
            pick = f"{fade_team} {line:+g}"
        else:
            odds = game.away_ml if fade == "away" else game.home_ml
            if odds is None:
                continue
            line = None
            pick = f"{fade_team} ML"

        tier = classify(game.league, mkt, pt, pm)
        row = {
            "league": game.league.upper(), "start": game.start, "matchup": f"{game.away} @ {game.home}",
            "market": "Spread" if mkt == "spread" else "Moneyline",
            "public_side": pub_team, "public_bets_%": pt, "public_money_%": pm,
            "money_minus_bets": pm - pt, "fade_pick": pick, "fade_line": line, "odds": odds,
            "tier": tier, "tier_label": TIERS[tier]["label"], "units": TIERS[tier]["units"],
            "status": game.status, "game_id": game.game_id, "season": game.season, "week": game.week,
        }
        row.update(grade_row(game, mkt, fade, line, odds))
        rows.append(row)
    return rows


def grade_row(game: Game, mkt: str, fade: str, line, odds) -> dict:
    if game.status != "complete" or game.away_score is None or game.home_score is None:
        return {"result": None, "profit_1u": None}
    a, h = float(game.away_score), float(game.home_score)
    fade_margin = (a - h) if fade == "away" else (h - a)
    if mkt == "spread":
        m = fade_margin + line
    else:
        m = fade_margin
    if m > 0:
        return {"result": "W", "profit_1u": round(_payout(odds), 3)}
    if m < 0:
        return {"result": "L", "profit_1u": -1.0}
    return {"result": "P", "profit_1u": 0.0}


def _fmt(v):
    return "—" if v is None else f"{v:+g}"


def game_rows(game: Game) -> list[dict]:
    """Every market for a game: the fade signal if there is one, otherwise a GROUNDED row."""
    sig = {r["market"]: r for r in signals(game)}
    lines = {
        "Spread": (f"{game.away} {_fmt(game.away_spread)} / {game.home} "
                   f"{_fmt(None if game.away_spread is None else -game.away_spread)}"),
        "Moneyline": f"{game.away} {_fmt(game.away_ml)} / {game.home} {_fmt(game.home_ml)}",
    }
    splits = {"Spread": (game.spread_away_tickets, game.spread_away_money),
              "Moneyline": (game.ml_away_tickets, game.ml_away_money)}
    rows = []
    for mkt in ("Spread", "Moneyline"):
        if mkt in sig:
            row = sig[mkt]
        else:
            at, am = splits[mkt]
            has = at is not None and am is not None and not (at == 0 and am == 0)
            if has:
                away_more = at >= 50
                pub_team = game.away if away_more else game.home
                pt, pm = (at, am) if away_more else (100 - at, 100 - am)
            else:
                pub_team, pt, pm = None, None, None
            row = {
                "league": game.league.upper(), "start": game.start, "matchup": f"{game.away} @ {game.home}",
                "market": mkt, "public_side": pub_team, "public_bets_%": pt, "public_money_%": pm,
                "money_minus_bets": None if pt is None else pm - pt,
                "fade_pick": "No 70% side" if has else "No splits yet", "fade_line": None, "odds": None,
                "tier": "GROUNDED", "tier_label": TIERS["GROUNDED"]["label"], "units": 0.0,
                "status": game.status, "game_id": game.game_id, "season": game.season, "week": game.week,
                "result": None, "profit_1u": None,
            }
        row["line"] = lines[mkt]
        row["score"] = (f"{game.away} {game.away_score:g} – {game.home} {game.home_score:g}"
                        if game.away_score is not None and game.home_score is not None
                        and game.status != "scheduled" else "")
        rows.append(row)
    return rows


def board(games: list[Game]) -> list[dict]:
    """Every game and market on the slate, in start-time order."""
    rows = [r for g in games for r in game_rows(g)]
    return sorted(rows, key=lambda r: (r["start"] or "", r["matchup"], r["market"] != "Spread"))


# ---------------------------------------------------------------- history / backtest
SPLITS_START = dt.date(2023, 10, 1)  # Action Network splits history begins ~fall 2023


def nba_dates(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.month not in (7, 8, 9):  # offseason
            yield d
        d += dt.timedelta(days=1)


def history(leagues=("nfl", "nba"), start: dt.date = SPLITS_START, end: dt.date | None = None,
            nfl_seasons=None, progress=None, pause: float = 0.15) -> list[dict]:
    """All graded fade signals over a period. ~1 request per NBA day / NFL week."""
    end = end or dt.date.today() - dt.timedelta(days=1)
    jobs = []
    if "nfl" in leagues:
        seasons = nfl_seasons or list(range(start.year, end.year + 1))
        for s in seasons:
            jobs += [("nfl", dict(season=s, week=w, season_type="reg")) for w in range(1, 19)]
            jobs += [("nfl", dict(season=s, week=w, season_type="post")) for w in range(1, 5)]
    if "nba" in leagues:
        jobs += [("nba", dict(date=d)) for d in nba_dates(start, end)]

    seen, rows = set(), []
    for i, (lg, kw) in enumerate(jobs):
        try:
            games = fetch_nfl(**kw) if lg == "nfl" else fetch_nba(kw["date"])
        except Exception:
            games = []
        for g in games:
            if g.game_id in seen or g.status != "complete":
                continue
            seen.add(g.game_id)
            rows += [r for r in signals(g) if r["result"] is not None]
        if progress:
            progress((i + 1) / len(jobs))
        time.sleep(pause)
    return rows


def summarize(rows: list[dict], by=("league", "market", "tier")):
    """Record / win% / units / ROI grouped by keys."""
    agg = {}
    for r in rows:
        k = tuple(r[b] for b in by)
        a = agg.setdefault(k, {"bets": 0, "W": 0, "L": 0, "P": 0, "units": 0.0})
        a["bets"] += 1
        a[r["result"]] += 1
        a["units"] += r["profit_1u"]
    out = []
    for k, a in sorted(agg.items()):
        dec = a["W"] + a["L"]
        out.append({**dict(zip(by, k)), "bets": a["bets"], "record": f'{a["W"]}-{a["L"]}-{a["P"]}',
                    "win_%": round(100 * a["W"] / dec, 1) if dec else None,
                    "units": round(a["units"], 1), "roi_%": round(100 * a["units"] / a["bets"], 1)})
    return out


def _cli():
    import argparse, csv, sys
    ap = argparse.ArgumentParser(description="Rocket Science public-fade model")
    ap.add_argument("cmd", choices=["today", "backtest"])
    ap.add_argument("--out", help="CSV path for backtest rows")
    args = ap.parse_args()
    if args.cmd == "today":
        today = dt.date.today()
        rows = board(fetch_nba(today) + fetch_nfl())
        for r in rows:
            if r["tier"] in ("LIFTOFF", "ORBIT"):
                print(f'{r["tier_label"]:12} {r["league"]} {r["matchup"]:12} {r["market"]:9} '
                      f'FADE -> {r["fade_pick"]} ({r["odds"]:+g})  public {r["public_bets_%"]:.0f}% bets / '
                      f'{r["public_money_%"]:.0f}% money  [{r["status"]}]')
        print(f"{len(rows)} public sides flagged; "
              f"{sum(r['tier'] in ('LIFTOFF', 'ORBIT') for r in rows)} plays.")
    else:
        rows = history(progress=lambda p: print(f"\r{p:5.0%}", end="", file=sys.stderr))
        print(file=sys.stderr)
        for s in summarize(rows):
            print(s)
        if args.out and rows:
            with open(args.out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)


if __name__ == "__main__":
    _cli()
