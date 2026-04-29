#!/usr/bin/env python3
"""
Fetches Seattle Mariners box scores for the current season from the MLB Stats API
and writes them to data/scores.json for the static site.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────
MARINERS_ID = 136
SEASON = datetime.now().year
BASE = "https://statsapi.mlb.com/api/v1"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "scores.json")
# ──────────────────────────────────────────────────────────────────────────────


def fetch(url: str, retries: int = 3) -> dict:
    from urllib.request import Request
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MarinersScores/1.0)",
        "Accept": "application/json",
    }
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except (URLError, Exception) as e:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries} for {url}: {e}")
            time.sleep(2 ** attempt)


def get_schedule() -> list[dict]:
    """Fetch the full-season schedule for the Mariners (all game types)."""
    url = (
        f"{BASE}/schedule"
        f"?sportId=1&teamId={MARINERS_ID}&season={SEASON}"
        f"&gameType=S,R,P"
        f"&hydrate=linescore,decisions,probablePitcher"
    )
    data = fetch(url)
    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            games.append(g)
    return games


def get_boxscore(game_pk: int) -> dict:
    url = f"{BASE}/game/{game_pk}/boxscore"
    return fetch(url)


def parse_linescore(ls: dict, home_id: int, away_id: int) -> dict | None:
    if not ls:
        return None
    innings = []
    for inn in ls.get("innings", []):
        innings.append({
            "num": inn.get("num"),
            "away": inn.get("away", {}).get("runs"),
            "home": inn.get("home", {}).get("runs"),
        })
    teams = ls.get("teams", {})
    return {
        "innings": innings,
        "away": {
            "runs": teams.get("away", {}).get("runs"),
            "hits": teams.get("away", {}).get("hits"),
            "errors": teams.get("away", {}).get("errors"),
        },
        "home": {
            "runs": teams.get("home", {}).get("runs"),
            "hits": teams.get("home", {}).get("hits"),
            "errors": teams.get("home", {}).get("errors"),
        },
        "currentInning": ls.get("currentInning"),
        "currentInningOrdinal": ls.get("currentInningOrdinal"),
        "inningState": ls.get("inningState", ""),
    }


def parse_boxscore_players(bs: dict, side: str) -> tuple[list, list]:
    """Returns (batters, pitchers) for the given side ('home' or 'away')."""
    team = bs.get("teams", {}).get(side, {})
    batters_order = team.get("battingOrder", [])
    players = team.get("players", {})

    batters = []
    for pid in batters_order:
        key = f"ID{pid}"
        p = players.get(key, {})
        person = p.get("person", {})
        pos = p.get("position", {}).get("abbreviation", "")
        s = p.get("stats", {}).get("batting", {})
        batters.append({
            "name": person.get("fullName", ""),
            "pos": pos,
            "ab": s.get("atBats"),
            "r": s.get("runs"),
            "h": s.get("hits"),
            "rbi": s.get("rbi"),
            "bb": s.get("baseOnBalls"),
            "so": s.get("strikeOuts"),
            "avg": s.get("avg"),
        })

    pitchers_order = team.get("pitchers", [])
    pitchers = []
    for pid in pitchers_order:
        key = f"ID{pid}"
        p = players.get(key, {})
        person = p.get("person", {})
        s = p.get("stats", {}).get("pitching", {})
        pitchers.append({
            "name": person.get("fullName", ""),
            "ip": s.get("inningsPitched"),
            "h": s.get("hits"),
            "r": s.get("runs"),
            "er": s.get("earnedRuns"),
            "bb": s.get("baseOnBalls"),
            "so": s.get("strikeOuts"),
            "era": s.get("era"),
        })

    return batters, pitchers


def build_game_record(g: dict) -> dict | None:
    """Build a rich game record from schedule + boxscore data."""
    status = g.get("status", {})
    detail = status.get("detailedState", "")
    abstract = status.get("abstractGameState", "")

    home_team = g.get("teams", {}).get("home", {}).get("team", {})
    away_team = g.get("teams", {}).get("away", {}).get("team", {})
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    is_mariners_home = home_id == MARINERS_ID

    game_pk = g.get("gamePk")
    game_date = g.get("gameDate", "")  # ISO datetime string
    date_only = game_date[:10] if game_date else ""
    venue = g.get("venue", {}).get("name", "")
    game_type = g.get("gameType", "R")  # S=spring, R=regular, P=playoff

    linescore_raw = g.get("linescore", {})
    linescore = parse_linescore(linescore_raw, home_id, away_id)

    home_score = g.get("teams", {}).get("home", {}).get("score")
    away_score = g.get("teams", {}).get("away", {}).get("score")

    decisions = g.get("decisions", {})
    winner = decisions.get("winner", {}).get("fullName") if decisions else None
    loser = decisions.get("loser", {}).get("fullName") if decisions else None
    save = decisions.get("save", {}).get("fullName") if decisions else None

    # Determine if game is final / completed
    is_final = abstract == "Final"

    # Fetch detailed boxscore for completed games
    batters_home, pitchers_home = [], []
    batters_away, pitchers_away = [], []
    if is_final and game_pk:
        try:
            bs = get_boxscore(game_pk)
            batters_home, pitchers_home = parse_boxscore_players(bs, "home")
            batters_away, pitchers_away = parse_boxscore_players(bs, "away")
        except Exception as e:
            print(f"  Warning: could not fetch boxscore for {game_pk}: {e}")

    # Mariners as home or away
    mariners_score = home_score if is_mariners_home else away_score
    opp_score = away_score if is_mariners_home else home_score
    opp_team = away_team if is_mariners_home else home_team
    opp_name = opp_team.get("name", "")
    opp_abbr = opp_team.get("abbreviation", "")

    result = None
    if is_final and mariners_score is not None and opp_score is not None:
        result = "W" if mariners_score > opp_score else "L"

    return {
        "gamePk": game_pk,
        "date": date_only,
        "gameDateTime": game_date,
        "gameType": game_type,
        "status": detail,
        "abstractState": abstract,
        "isFinal": is_final,
        "isHome": is_mariners_home,
        "venue": venue,
        "opponent": {
            "id": opp_team.get("id"),
            "name": opp_name,
            "abbr": opp_abbr,
        },
        "mariners": {
            "score": mariners_score,
            "side": "home" if is_mariners_home else "away",
        },
        "opponent_score": opp_score,
        "result": result,
        "linescore": linescore,
        "decisions": {
            "winner": winner,
            "loser": loser,
            "save": save,
        },
        "batters": {
            "home": batters_home if is_mariners_home else batters_away,
            "away": batters_away if is_mariners_home else batters_home,
            "homeLabel": "Seattle Mariners" if is_mariners_home else opp_name,
            "awayLabel": opp_name if is_mariners_home else "Seattle Mariners",
        },
        "pitchers": {
            "home": pitchers_home if is_mariners_home else pitchers_away,
            "away": pitchers_away if is_mariners_home else pitchers_home,
        },
    }


def main():
    print(f"Fetching {SEASON} Seattle Mariners schedule …")
    games = get_schedule()
    print(f"  Found {len(games)} games on schedule.")

    records = []
    wins = 0
    losses = 0

    for i, g in enumerate(games, 1):
        pk = g.get("gamePk", "?")
        date = g.get("gameDate", "")[:10]
        state = g.get("status", {}).get("abstractGameState", "")
        print(f"  [{i:>3}/{len(games)}] {date}  pk={pk}  state={state}")

        try:
            rec = build_game_record(g)
            if rec:
                records.append(rec)
                if rec["gameType"] == "R":  # only count regular season games
                    if rec["result"] == "W":
                        wins += 1
                    elif rec["result"] == "L":
                        losses += 1
        except Exception as e:
            print(f"    ERROR: {e}")

    records.sort(key=lambda r: r["date"])

    output = {
        "season": SEASON,
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record": {"wins": wins, "losses": losses},
        "games": records,
    }

    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT)), exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    print(f"\nWrote {len(records)} games to {OUTPUT}")
    print(f"Season record: {wins}–{losses}")


if __name__ == "__main__":
    main()
  
