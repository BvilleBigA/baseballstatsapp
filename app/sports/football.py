"""
Football sport plugin.

Source of truth: the GWT live-stats boxscore blob persisted on
``Game.gwt_bs_blob`` (the same JSON shape produced by Presto LiveStats).
The blob contains everything the box-score, scoring summary, drive chart,
XML export, and PDF report need.

Outputs:
    XML  — Presto-style ``<fbgame>`` (matches downloadXML.xml)
    JSON — the blob, augmented with game-level metadata from the DB
    HTML — sport-specific boxscore_print_football.html template
"""

from __future__ import annotations

import json as _json
import re
import xml.etree.ElementTree as ET
from datetime import date as date_cls
from typing import Any, Dict, List, Optional, Tuple

from app.sports.base import SportPlugin
from app.sports.football_pbp import decode_play


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_blob(game) -> Dict[str, Any]:
    """Parse ``Game.gwt_bs_blob`` into a dict. Returns ``{}`` on failure."""
    raw = getattr(game, "gwt_bs_blob", "") or ""
    if not raw:
        return {}
    try:
        return _json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _team_blob(blob: Dict[str, Any], vh: str) -> Dict[str, Any]:
    """Return the visitor (``vh='V'``) or home (``vh='H'``) team dict from the blob."""
    teams = blob.get("teams") or []
    if not teams:
        return {}
    # Convention: first team is visitor, second is home. Cross-check with keyStroke
    # if available — Presto uses a single-letter key per team.
    if vh == "V":
        return teams[0]
    if len(teams) > 1:
        return teams[1]
    return {}


def _ext_id(team) -> str:
    """Stable external team id used in XML (Presto 'STATS1', 'STATS2' style)."""
    if not team:
        return ""
    tid = (team.team_id or team.code or team.abbreviation or f"T{team.id}").strip()
    return tid or f"T{team.id}"


def _venue_date(date_str: str) -> str:
    """YYYY-MM-DD → M/D/YYYY (or pass-through)."""
    if not date_str:
        return ""
    try:
        y, m, d = date_str.split("-")
        return f"{int(m)}/{int(d)}/{y}"
    except Exception:
        return date_str


def _short_name(full: str, max_len: int = 15) -> str:
    """Mirror Presto's 'shortname' truncation."""
    return (full or "")[:max_len]


def _checkname(full: str) -> str:
    """Presto's checkname format: 'LASTNAME,FIRSTNAME' uppercase."""
    if not full:
        return ""
    parts = (full or "").strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0].upper()
    return f"{parts[-1].upper()},{' '.join(parts[:-1]).upper()}"


def _filtered_attrs(d: Dict[str, Any], keys: List[str]) -> Dict[str, str]:
    """Pick fields from a player/team dict, dropping zero values to match Presto."""
    out = {}
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)) and v == 0:
            continue
        out[k] = str(v)
    return out


def _indent(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print ET tree (in-place)."""
    indent = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        for child in elem:
            _indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent + "  "
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


# ── XML stat-block mappings (Presto field names) ─────────────────────────────
# Each tuple is (xml_attr, json_field). Zero values are dropped.

_TEAM_TOTAL_HEAD = [
    ("totoff_plays", "totalOffPlays"),
    ("totoff_yards", "totalOffYards"),
]

_BLOCKS = {
    "firstdowns": [
        ("no", "firstDownNo"), ("rush", "firstDownRush"),
        ("pass", "firstDownPass"), ("penalty", "firstDownPenalty"),
    ],
    "penalties": [("no", "penaltyNo"), ("yds", "penaltyYards")],
    "conversions": [
        ("thirdconv", "conversionThirdConv"), ("thirdatt", "conversionThirdAtt"),
        ("fourthconv", "conversionFourthConv"), ("fourthatt", "conversionFourthAtt"),
    ],
    "fumbles": [("no", "fumblesNo"), ("lost", "fumblesLost")],
    "misc": [("yds", "miscYards")],   # 'top' is computed below
    "redzone": [
        ("att", "redZoneTimeInside20"), ("scores", "redZoneTimeScored"),
        ("points", "redZonePointsScored"), ("tdrush", "redZoneRushingTds"),
        ("tdpass", "redZonePassingTds"), ("fgmade", "redZoneFgsMade"),
        ("endfga", "redZoneEndOnFga"), ("enddowns", "redZoneEndOnDowns"),
        ("endint", "redZoneEndOnIntCpt"), ("endfumb", "redZoneEndOnFumble"),
        ("endhalf", "redZoneEndOfHalf"), ("endgame", "redZoneEndOfGame"),
    ],
    "rush": [
        ("att", "rushAtt"), ("td", "rushTd"), ("long", "rushLong"),
        ("loss", "rushWinLoss"), ("yds", "rushYards"),
    ],
    "pass": [
        ("att", "passAtt"), ("td", "passTd"), ("long", "passLong"),
        ("yds", "passYards"), ("comp", "passComp"),
        ("sacks", "passSacks"), ("sackyds", "passSackYards"),
        ("int", "passInt"),
    ],
    "rcv": [
        ("long", "receivingLong"), ("no", "receivingNo"),
        ("td", "receivingTd"), ("yds", "receivingYards"),
    ],
    "punt": [
        ("avg", "kickPuntAvg"), ("blkd", "kickPuntBlkd"), ("fc", "kickPuntFc"),
        ("inside20", "kickPuntI20"), ("long", "kickPuntLong"),
        ("no", "kickPuntNo"), ("plus50", "kickPunt50"),
        ("tb", "kickPuntTb"), ("yds", "kickPuntYards"),
    ],
    "ko": [
        ("no", "kickKoNo"), ("ob", "kickKoOb"),
        ("tb", "kickKoTb"), ("yds", "kickKoYards"),
    ],
    "fg": [
        ("att", "kickFgAtt"), ("blkd", "kickFgBlk"),
        ("long", "kickFgLong"), ("made", "kickFgMad"),
    ],
    "pat": [
        ("kickatt", "epOffKickAt"), ("kickmade", "epOffKickMd"),
        ("passatt", "epOffPassAt"), ("passmade", "epOffPassMd"),
        ("rcvmade", "epOffRcvMd"), ("rushatt", "epOffRushAt"),
        ("rushmade", "epOffRushMd"),
        # Defensive PAT returns
        ("retfatt", "epDefIfAt"), ("retfmade", "epDefRetMad"),
        ("retkatt", "epDefKickRetAt"), ("retkmade", "epDefKickRetMd"),
    ],
    "defense": [
        ("brup", "defensePassBrUp"), ("ff", "defenseFumbForc"),
        ("fr", "defenseFumbRcvr"), ("fryds", "returnsFumbYards"),
        ("qbh", "defenseQbh"),
        ("sacks", "defenseTotalSack"), ("sackyds", "defenseSackWinLossYards"),
        ("sacka", "defenseSackA"), ("sacksa", "defenseSackA"),
        ("sackua", "defenseSackUa"), ("sacksua", "defenseSackUa"),
        ("tacka", "defenseTackA"), ("tackua", "defenseTackUa"),
        ("tot_tack", "defenseTackUaA"),
        ("tfla", "defenseTflA"), ("tflua", "defenseTflUa"),
        ("tflyds", "defenseTflLossYards"),
    ],
    "kr": [
        ("long", "returnsKickLong"), ("no", "returnsKickNo"),
        ("td", "returnsKickTd"), ("yds", "returnsKickYards"),
    ],
    "pr": [
        ("long", "returnsPuntLong"), ("no", "returnsPuntNo"),
        ("td", "returnsPuntTd"), ("yds", "returnsPuntYards"),
    ],
    "fr": [
        ("long", "returnsFumbLong"), ("no", "returnsFumbNo"),
        ("td", "returnsFumbTd"), ("yds", "returnsFumbYards"),
    ],
    "ir": [
        ("long", "returnsIntLong"), ("no", "returnsIntNo"),
        ("td", "returnsIntTd"), ("yds", "returnsIntYards"),
    ],
}

# Blocks to consider including in a per-player <player> element. Order matters
# for matching Presto's downloadXML.xml output.
_PLAYER_BLOCK_ORDER = [
    "rush", "pass", "rcv", "punt", "ko", "fg", "pat",
    "defense", "kr", "pr", "fr", "ir", "fumbles",
]


def _has_any_value(player: Dict[str, Any], attr_map: List[Tuple[str, str]]) -> bool:
    return any(player.get(k) for _, k in attr_map)


# XML attributes that represent yards "lost" — Presto stores them as positive
# magnitudes (loss="10", sackyds="9", tflyds="3"). The GWT blob sometimes
# carries them as negative deltas. Normalise to positive.
_ABS_LOSS_ATTRS = {
    "loss", "sackyds", "tflyds", "fryds",
    "sackwinloss", "tflwinloss",
}


def _emit_block(parent: ET.Element, tag: str, src: Dict[str, Any], mapping: List[Tuple[str, str]]) -> Optional[ET.Element]:
    attrs: Dict[str, str] = {}
    seen = set()
    for xml_k, json_k in mapping:
        if xml_k in seen:
            continue
        seen.add(xml_k)
        v = src.get(json_k)
        if v is None:
            continue
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, (int, float)) and v == 0:
            continue
        if xml_k in _ABS_LOSS_ATTRS and isinstance(v, (int, float)):
            v = abs(v)
        attrs[xml_k] = str(v)
    # Recompute averages from no + yds — the GWT-stored value is scaled
    # inconsistently between team-level and per-player rows.
    if tag in ("punt", "ko") and "no" in attrs and "yds" in attrs:
        try:
            n = int(attrs["no"]); y = int(attrs["yds"])
            if n:
                attrs["avg"] = str(round(y / n, 1))
        except (ValueError, ZeroDivisionError):
            pass
    if not attrs:
        return None
    el = ET.SubElement(parent, tag)
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _format_top(secs: int) -> str:
    """seconds → mm:ss"""
    try:
        s = int(secs or 0)
    except (TypeError, ValueError):
        return "00:00"
    return f"{s // 60:02d}:{s % 60:02d}"


# ── Plugin ───────────────────────────────────────────────────────────────────

class FootballPlugin(SportPlugin):
    sport_id = 0
    name = "Football"
    xml_root = "fbgame"
    boxscore_template = "boxscore_print_football.html"
    statboxscore_template = "boxscore_print_football.html"

    # ── XML ──────────────────────────────────────────────────────────────────
    def build_xml(self, game) -> str:
        blob = _safe_blob(game)
        d = date_cls.today()
        root = ET.Element("fbgame")
        root.set("source", "Gameday LiveStats")
        root.set("version", "7.16.0")
        root.set("generated", f"{d.month:02d}/{d.day}/{d.year}")

        vis = game.visitor_team
        home = game.home_team
        vis_id = _ext_id(vis)
        home_id = _ext_id(home)
        vis_blob = _team_blob(blob, "V")
        home_blob = _team_blob(blob, "H")

        ev = (blob.get("eventInfo") or {})
        self._write_venue(root, game, ev, vis, home, vis_id, home_id)
        self._write_status(root, ev)
        self._write_team(root, "V", vis, vis_blob, home_blob, vis_id, home_id, blob)
        self._write_team(root, "H", home, home_blob, vis_blob, vis_id, home_id, blob)
        self._write_scores(root, ev, vis_blob, home_blob, vis_id, home_id)
        self._write_fgas(root, ev, vis_blob, home_blob, vis_id, home_id)
        self._write_drives(root, ev, vis_id, home_id)
        self._write_plays(root, blob, vis_id, home_id, vis_blob, home_blob)

        _indent(root)
        body = ET.tostring(root, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n\n' + body + "\n"

    # ── JSON ─────────────────────────────────────────────────────────────────
    def build_json(self, game) -> Dict[str, Any]:
        """Return the persisted GWT boxscore blob, augmented with DB metadata.

        The blob is the canonical source — returning it verbatim ensures the
        Presto/GWT clients (and offline tooling) can round-trip cleanly."""
        blob = _safe_blob(game)
        if not blob:
            return {"teams": [], "plays": {}, "eventInfo": {}, "countPeriods": 0, "gamePeriods": 0}

        ev = blob.setdefault("eventInfo", {})
        ev.setdefault("date", _venue_date(game.date or ""))
        ev.setdefault("location", game.location or "")
        ev.setdefault("attendance", game.attendance or 0)
        ev.setdefault("conference", bool(game.is_league_game))
        ev.setdefault("neutral", bool(game.is_neutral))
        ev.setdefault("night", bool(game.is_night))
        ev.setdefault("exhibition", bool(game.is_exhibition))
        ev.setdefault("status", "Final" if game.is_complete else "In Progress")
        ev.setdefault("statusCode", 2 if game.is_complete else 1)
        ev.setdefault("sportCode", "fb")
        ev.setdefault("id", game.id)
        season = getattr(game, "season", None)
        if season is not None:
            ev.setdefault("seasonId", season.id)
        return blob

    # ── Boxscore data (templates) ────────────────────────────────────────────
    def build_boxscore_data(self, game) -> Dict[str, Any]:
        blob = _safe_blob(game)
        vis_blob = _team_blob(blob, "V")
        home_blob = _team_blob(blob, "H")
        ev = blob.get("eventInfo") or {}

        vis_name = (vis_blob.get("name") or (game.visitor_team.name if game.visitor_team else "Visitor"))
        home_name = (home_blob.get("name") or (game.home_team.name if game.home_team else "Home"))

        # Linescore by quarter
        n_prd = max(int(ev.get("rulesPeriods") or 4),
                    int(ev.get("statusPeriod") or 0),
                    len(vis_blob.get("periodstats") or []) or 0,
                    len(home_blob.get("periodstats") or []) or 0,
                    4)
        v_scores = [int((p.get("score") or 0)) for p in (vis_blob.get("periodstats") or [])]
        h_scores = [int((p.get("score") or 0)) for p in (home_blob.get("periodstats") or [])]
        while len(v_scores) < n_prd: v_scores.append(0)
        while len(h_scores) < n_prd: h_scores.append(0)

        linescore = []
        for i in range(n_prd):
            linescore.append({"num": i + 1, "v": v_scores[i], "h": h_scores[i]})
        v_total = sum(v_scores)
        h_total = sum(h_scores)

        # Team stats rollup for the print template
        team_summary = self._team_summary(vis_blob, home_blob)

        # Per-team player stat tables
        vis_players = self._player_tables(vis_blob)
        home_players = self._player_tables(home_blob)

        # Scoring summary
        scoring = self.scoring_summary(game)

        # Drive chart
        drives = self._drive_chart(ev, vis_blob, home_blob)

        data: Dict[str, Any] = {
            "sport_id": 0,
            "sport_name": "Football",
            "visitor_name": vis_name,
            "home_name": home_name,
            "visitor_runs": v_total,
            "home_runs": h_total,
            "visitor_score": v_total,
            "home_score": h_total,
            "visitor_record": vis_blob.get("record") or (game.visitor_record or ""),
            "home_record": home_blob.get("record") or (game.home_record or ""),
            "visitor_abbr": vis_blob.get("keyStroke") or (game.visitor_team.abbreviation if game.visitor_team else ""),
            "home_abbr": home_blob.get("keyStroke") or (game.home_team.abbreviation if game.home_team else ""),
            "visitor_id": vis_blob.get("abbr") or _ext_id(game.visitor_team),
            "home_id": home_blob.get("abbr") or _ext_id(game.home_team),
            "date": _venue_date(game.date or "") or ev.get("date") or "",
            "start_time": game.start_time or ev.get("timeStart") or "",
            "location": game.location or ev.get("location") or "",
            "attendance": ev.get("attendance") or game.attendance or 0,
            "status_label": game.status_label or "",
            "linescore": linescore,
            "team_summary": team_summary,
            "visitor_players": vis_players,
            "home_players": home_players,
            "scoring_summary": scoring,
            "drives": drives,
            "officials": (ev.get("referees") or []),
            "n_periods": n_prd,
        }
        return data

    # ── Scoring summary ──────────────────────────────────────────────────────
    def scoring_summary(self, game) -> List[Dict[str, Any]]:
        blob = _safe_blob(game)
        vis_blob = _team_blob(blob, "V")
        home_blob = _team_blob(blob, "H")
        ev = blob.get("eventInfo") or {}
        scoring_raw = ev.get("scoring") or []
        rows = []
        for s in scoring_raw:
            home_team = bool(s.get("homeTeam"))
            team_blob = home_blob if home_team else vis_blob
            team_name = team_blob.get("name") or ("Home" if home_team else "Visitor")
            scorer = self._player_label(team_blob, str(s.get("scorer") or ""))
            passer = self._player_label(team_blob, str(s.get("passer") or ""))
            patby  = self._player_label(team_blob, str(s.get("patBy") or ""))
            how = (s.get("how") or "").upper()
            stype = (s.get("type") or "").upper()
            yds = s.get("yards")
            clock = f"{int(s.get('mins') or 0):02d}:{int(s.get('secs') or 0):02d}"
            qtr = int(s.get("quarter") or 0)
            v_score = int(s.get("visitorScore") or 0)
            h_score = int(s.get("homeScore") or 0)

            if stype == "TD" and how == "PASS":
                txt = f"{team_name} — {passer} {yds}yd TD pass to {scorer}"
            elif stype == "TD" and how == "RUSH":
                txt = f"{team_name} — {scorer} {yds}yd TD run"
            elif stype == "TD" and how in ("FUMB", "FUMBLE"):
                txt = f"{team_name} — {scorer} {yds}yd fumble return TD"
            elif stype == "TD" and how in ("INT", "INTERCEPTION"):
                txt = f"{team_name} — {scorer} {yds}yd interception return TD"
            elif stype == "TD":
                txt = f"{team_name} — {scorer} {yds}yd TD" if yds else f"{team_name} — {scorer} TD"
            elif stype == "FG":
                txt = f"{team_name} — {scorer} {yds}yd field goal"
            elif stype == "SAF":
                txt = f"{team_name} — Safety"
            else:
                txt = f"{team_name} — {scorer} {stype}"

            # Append PAT info when this was a TD with a PAT attempt
            patres = (s.get("patres") or "").upper()
            if stype == "TD":
                # patCode is the GWT internal mapping; fall back to plain "good/no good" lookup
                pat_kind = self._pat_kind_from_code(s.get("patCode"))
                if patby:
                    if patres in ("FAIL", "F", "N", "NG"):
                        txt += f" — 2pt {pat_kind} by {patby} failed" if pat_kind == "pass" else f" — {patby} kick failed"
                    elif pat_kind == "pass":
                        txt += f" — 2pt pass conversion ({patby}) good"
                    elif pat_kind == "rush":
                        txt += f" — 2pt rush conversion ({patby}) good"
                    else:
                        txt += f" — {patby} kick good"

            rows.append({
                "prd": qtr,
                "time": clock,
                "team": team_name,
                "team_visitor": not home_team,
                "scorer": scorer,
                "passer": passer,
                "pat_by": patby,
                "how": how,
                "type": stype,
                "yards": yds,
                "text": txt,
                "vscore": v_score,
                "hscore": h_score,
                "score": f"{v_score} - {h_score}",
            })
        return rows

    # ── Play-by-play ─────────────────────────────────────────────────────────
    def play_by_play(self, game) -> List[Dict[str, Any]]:
        """Return narrative PBP suitable for the per-quarter summary section."""
        blob = _safe_blob(game)
        vis_blob = _team_blob(blob, "V")
        home_blob = _team_blob(blob, "H")
        vis_id = vis_blob.get("abbr") or "STATS1"
        home_id = home_blob.get("abbr") or "STATS2"

        plays_by_period = blob.get("plays") or {}
        rows: List[Dict[str, Any]] = []
        for prd_key in sorted(plays_by_period.keys(), key=lambda k: int(k)):
            for play in plays_by_period[prd_key]:
                props = play.get("props") or {}
                raw = props.get("RAW_PLAY") or ""
                pos_is_home = _possession_is_home(props, play)
                pos = home_blob if pos_is_home else vis_blob
                deff = vis_blob if pos_is_home else home_blob
                text = decode_play(
                    raw,
                    possession_team=pos,
                    defense_team=deff,
                    vis_id=vis_id, home_id=home_id,
                    vis_blob=vis_blob, home_blob=home_blob,
                )
                if text is None:
                    text = props.get("COMMENT") or props.get("CMT") or ""
                if not text:
                    # Pure marker (drive/quarter/spot); skip so PBP stays clean.
                    continue
                rows.append({
                    "period": int(play.get("period") or prd_key),
                    "clock": props.get("CLOCK") or "",
                    "team_v": not pos_is_home,
                    "text": text,
                    "vscore": _safe_int(props.get("V_SCORE")),
                    "hscore": _safe_int(props.get("H_SCORE")),
                })
        return rows

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _player_label(self, team_blob: Dict[str, Any], uni: str) -> str:
        if not uni:
            return ""
        for p in team_blob.get("players", []):
            if str(p.get("uniform") or "") == str(uni):
                return p.get("completeName") or f"#{uni}"
        return f"#{uni}"

    def _pat_kind_from_code(self, code: Any) -> str:
        """Decode the Presto patCode to a human-readable kind.

        16 ≈ Kick (1pt), 17 ≈ Rush (2pt), 18/34 ≈ Pass (2pt). These are the
        most common; unknown codes fall through to 'kick'."""
        try:
            c = int(code) if code is not None else 0
        except (TypeError, ValueError):
            c = 0
        if c == 0:
            return ""
        if c in (16,):
            return "kick"
        if c in (17,):
            return "rush"
        if c in (18, 34):
            return "pass"
        return "kick"

    def _team_summary(self, v: Dict[str, Any], h: Dict[str, Any]) -> Dict[str, Any]:
        def stat(k):
            return {"v": v.get(k, 0) or 0, "h": h.get(k, 0) or 0}
        def fmt_rush(side):
            att = side.get("rushAtt", 0) or 0
            yds = side.get("rushYards", 0) or 0
            return f"{att}-{yds}"
        def fmt_pa(side):
            att = side.get("passAtt", 0) or 0
            comp = side.get("passComp", 0) or 0
            ints = side.get("passInt", 0) or 0
            return f"{att}-{comp}-{ints}"
        def fmt_total(side):
            return f"{side.get('totalOffPlays', 0) or 0}-{side.get('totalOffYards', 0) or 0}"
        def fmt_punt(side):
            n = side.get("kickPuntNo", 0) or 0
            yds = side.get("kickPuntYards", 0) or 0
            avg = round(yds / n, 1) if n else 0
            return f"{n}-{avg}"
        def fmt_pen(side):
            return f"{side.get('penaltyNo', 0) or 0}-{side.get('penaltyYards', 0) or 0}"
        def fmt_fum(side):
            return f"{side.get('fumblesNo', 0) or 0}-{side.get('fumblesLost', 0) or 0}"
        def fmt_top(side):
            secs = side.get("miscPossession", 0) or 0
            try:
                secs = int(secs)
            except (TypeError, ValueError):
                secs = 0
            return f"{secs // 60}:{secs % 60:02d}"
        def fmt_pct(num, den):
            return f"{round((num / den * 100), 1) if den else 0}% ({num} of {den})"

        return {
            "first_downs": stat("firstDownNo"),
            "first_downs_rush": stat("firstDownRush"),
            "first_downs_pass": stat("firstDownPass"),
            "first_downs_penalty": stat("firstDownPenalty"),
            "rush_line": {"v": fmt_rush(v), "h": fmt_rush(h)},
            "pass_yards": stat("passYards"),
            "pass_line": {"v": fmt_pa(v), "h": fmt_pa(h)},
            "total_offense": stat("totalOffYards"),
            "total_offense_line": {"v": fmt_total(v), "h": fmt_total(h)},
            "fumble_returns": {"v": f"{v.get('returnsFumbNo', 0) or 0}-{v.get('returnsFumbYards', 0) or 0}",
                               "h": f"{h.get('returnsFumbNo', 0) or 0}-{h.get('returnsFumbYards', 0) or 0}"},
            "punt_returns": {"v": f"{v.get('returnsPuntNo', 0) or 0}-{v.get('returnsPuntYards', 0) or 0}",
                             "h": f"{h.get('returnsPuntNo', 0) or 0}-{h.get('returnsPuntYards', 0) or 0}"},
            "kick_returns": {"v": f"{v.get('returnsKickNo', 0) or 0}-{v.get('returnsKickYards', 0) or 0}",
                             "h": f"{h.get('returnsKickNo', 0) or 0}-{h.get('returnsKickYards', 0) or 0}"},
            "int_returns": {"v": f"{v.get('returnsIntNo', 0) or 0}-{v.get('returnsIntYards', 0) or 0}",
                            "h": f"{h.get('returnsIntNo', 0) or 0}-{h.get('returnsIntYards', 0) or 0}"},
            "punts": {"v": fmt_punt(v), "h": fmt_punt(h)},
            "fumbles": {"v": fmt_fum(v), "h": fmt_fum(h)},
            "penalties": {"v": fmt_pen(v), "h": fmt_pen(h)},
            "possession": {"v": fmt_top(v), "h": fmt_top(h)},
            "third_down": {
                "v": fmt_pct(v.get("conversionThirdConv", 0) or 0, v.get("conversionThirdAtt", 0) or 0),
                "h": fmt_pct(h.get("conversionThirdConv", 0) or 0, h.get("conversionThirdAtt", 0) or 0),
            },
            "fourth_down": {
                "v": fmt_pct(v.get("conversionFourthConv", 0) or 0, v.get("conversionFourthAtt", 0) or 0),
                "h": fmt_pct(h.get("conversionFourthConv", 0) or 0, h.get("conversionFourthAtt", 0) or 0),
            },
            "red_zone": {
                "v": f"{v.get('redZoneTimeScored', 0) or 0}-{v.get('redZoneTimeInside20', 0) or 0}",
                "h": f"{h.get('redZoneTimeScored', 0) or 0}-{h.get('redZoneTimeInside20', 0) or 0}",
            },
        }

    def _player_tables(self, team_blob: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """Group per-player stat lines into the sections shown on the box score."""
        passing, rushing, receiving = [], [], []
        kicking, punting, ko_team = [], [], []
        kick_ret, punt_ret, int_ret, fum_ret = [], [], [], []
        defense, fumbles = [], []

        for p in team_blob.get("players", []):
            uni = str(p.get("uniform") or "")
            name = (p.get("completeName") or "").strip()
            if not name or name == "Team":
                pass  # include TEAM rows for defense / receiving / etc., skip elsewhere
            if (p.get("passAtt") or 0) > 0 or (p.get("passComp") or 0) > 0:
                passing.append({
                    "uni": uni, "name": name,
                    "cp": p.get("passComp", 0) or 0,
                    "att": p.get("passAtt", 0) or 0,
                    "yds": p.get("passYards", 0) or 0,
                    "td": p.get("passTd", 0) or 0,
                    "int": p.get("passInt", 0) or 0,
                    "long": p.get("passLong", 0) or 0,
                })
            if (p.get("rushAtt") or 0) > 0:
                att = p.get("rushAtt") or 0
                yds = p.get("rushYards") or 0
                rushing.append({
                    "uni": uni, "name": name, "att": att, "yds": yds,
                    "avg": round(yds / att, 1) if att else 0.0,
                    "long": p.get("rushLong", 0) or 0,
                    "td": p.get("rushTd", 0) or 0,
                })
            if (p.get("receivingNo") or 0) > 0:
                no = p.get("receivingNo") or 0
                yds = p.get("receivingYards") or 0
                receiving.append({
                    "uni": uni, "name": name, "no": no, "yds": yds,
                    "avg": round(yds / no, 1) if no else 0.0,
                    "long": p.get("receivingLong", 0) or 0,
                    "td": p.get("receivingTd", 0) or 0,
                })
            if (p.get("kickFgAtt") or 0) > 0 or (p.get("epOffKickAt") or 0) > 0:
                kicking.append({
                    "uni": uni, "name": name,
                    "fgm": p.get("kickFgMad", 0) or 0,
                    "fga": p.get("kickFgAtt", 0) or 0,
                    "lg": p.get("kickFgLong", 0) or 0,
                    "xpm": p.get("epOffKickMd", 0) or 0,
                    "xpa": p.get("epOffKickAt", 0) or 0,
                    "pts": (3 * (p.get("kickFgMad", 0) or 0)) + (p.get("epOffKickMd", 0) or 0),
                })
            if (p.get("kickPuntNo") or 0) > 0:
                n = p.get("kickPuntNo") or 0
                yds = p.get("kickPuntYards") or 0
                punting.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "avg": round(yds / n, 1) if n else 0.0,
                    "long": p.get("kickPuntLong", 0) or 0,
                    "tb": p.get("kickPuntTb", 0) or 0,
                    "in20": p.get("kickPuntI20", 0) or 0,
                })
            if (p.get("kickKoNo") or 0) > 0:
                n = p.get("kickKoNo") or 0
                yds = p.get("kickKoYards") or 0
                ko_team.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "avg": round(yds / n, 1) if n else 0.0,
                    "tb": p.get("kickKoTb", 0) or 0,
                    "ob": p.get("kickKoOb", 0) or 0,
                })
            if (p.get("returnsKickNo") or 0) > 0:
                n = p.get("returnsKickNo") or 0; yds = p.get("returnsKickYards") or 0
                kick_ret.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "avg": round(yds / n, 1) if n else 0.0,
                    "long": p.get("returnsKickLong", 0) or 0,
                    "td": p.get("returnsKickTd", 0) or 0,
                })
            if (p.get("returnsPuntNo") or 0) > 0:
                n = p.get("returnsPuntNo") or 0; yds = p.get("returnsPuntYards") or 0
                punt_ret.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "avg": round(yds / n, 1) if n else 0.0,
                    "long": p.get("returnsPuntLong", 0) or 0,
                    "td": p.get("returnsPuntTd", 0) or 0,
                })
            if (p.get("returnsIntNo") or 0) > 0:
                n = p.get("returnsIntNo") or 0; yds = p.get("returnsIntYards") or 0
                int_ret.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "avg": round(yds / n, 1) if n else 0.0,
                    "long": p.get("returnsIntLong", 0) or 0,
                    "td": p.get("returnsIntTd", 0) or 0,
                })
            if (p.get("returnsFumbNo") or 0) > 0:
                n = p.get("returnsFumbNo") or 0; yds = p.get("returnsFumbYards") or 0
                fum_ret.append({
                    "uni": uni, "name": name, "no": n, "yds": yds,
                    "long": p.get("returnsFumbLong", 0) or 0,
                    "td": p.get("returnsFumbTd", 0) or 0,
                })
            if (p.get("fumblesNo") or 0) > 0:
                fumbles.append({
                    "uni": uni, "name": name,
                    "no": p.get("fumblesNo", 0) or 0,
                    "lost": p.get("fumblesLost", 0) or 0,
                })

            d_total = ((p.get("defenseTackUa") or 0) + (p.get("defenseTackA") or 0))
            d_any = d_total or (p.get("defensePassBrUp") or 0) or (p.get("defenseFumbForc") or 0) \
                    or (p.get("defenseFumbRcvr") or 0) or (p.get("defenseQbh") or 0) \
                    or (p.get("defenseSackUa") or 0) or (p.get("defenseSackA") or 0) \
                    or (p.get("defenseTflUa") or 0) or (p.get("defenseTflA") or 0)
            if d_any:
                solo = p.get("defenseTackUa", 0) or 0
                ast = p.get("defenseTackA", 0) or 0
                sacks_u = p.get("defenseSackUa", 0) or 0
                sacks_a = p.get("defenseSackA", 0) or 0
                tfl_u = p.get("defenseTflUa", 0) or 0
                tfl_a = p.get("defenseTflA", 0) or 0
                sack_str = self._half_format(sacks_u, sacks_a, p.get("defenseSackWinLossYards"))
                tfl_str = self._half_format(tfl_u, tfl_a, p.get("defenseTflLossYards"))
                defense.append({
                    "uni": uni, "name": name,
                    "solo": solo, "ast": ast, "total": solo + ast,
                    "sacks": sack_str, "tfl": tfl_str,
                    "ff": p.get("defenseFumbForc", 0) or 0,
                    "fr": p.get("defenseFumbRcvr", 0) or 0,
                    "fr_yds": p.get("returnsFumbYards", 0) or 0,
                    "int": p.get("returnsIntNo", 0) or 0,
                    "int_yds": p.get("returnsIntYards", 0) or 0,
                    "brup": p.get("defensePassBrUp", 0) or 0,
                    "blks": p.get("defenseBlkdKick", 0) or 0,
                    "qbh": p.get("defenseQbh", 0) or 0,
                })
        return {
            "passing": passing, "rushing": rushing, "receiving": receiving,
            "kicking": kicking, "punting": punting, "kickoffs": ko_team,
            "kick_returns": kick_ret, "punt_returns": punt_ret,
            "int_returns": int_ret, "fumble_returns": fum_ret,
            "defense": defense, "fumbles": fumbles,
        }

    def _half_format(self, unassisted: int, assisted: int, yards: Any) -> str:
        """Format football half-sack/half-TFL totals as 'X.Y - YDS' or '-'."""
        u = float(unassisted or 0)
        a = float(assisted or 0)
        tot = u + (a / 2.0)
        if tot == 0:
            return "-"
        y = int(yards or 0)
        tot_str = (f"{tot:.1f}" if tot != int(tot) else f"{int(tot)}")
        if y:
            return f"{tot_str}-{y}"
        return tot_str

    def _drive_chart(self, ev: Dict[str, Any], vis_blob: Dict[str, Any], home_blob: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for d in ev.get("drives") or []:
            home_team = bool(d.get("homeTeam"))
            team = home_blob if home_team else vis_blob
            rows.append({
                "team": team.get("name") or ("Home" if home_team else "Visitor"),
                "team_v": not home_team,
                "plays": d.get("playsSum") or 0,
                "yards": d.get("yards") or 0,
                "top": _format_top(d.get("topSecs") or 0),
                "start_qtr": d.get("startQuarter"),
                "start_how": d.get("startHow"),
                "end_qtr": d.get("endQuarter"),
                "end_how": d.get("endHow"),
                "red_zone": bool(d.get("redZone")),
            })
        return rows

    # ── XML emitter pieces ───────────────────────────────────────────────────

    def _write_venue(self, root, game, ev, vis, home, vis_id, home_id):
        venue = ET.SubElement(root, "venue")
        venue.set("gameid", str(game.id or ""))
        venue.set("visid", vis_id)
        venue.set("visname", vis.name if vis else "")
        venue.set("homeid", home_id)
        venue.set("homename", home.name if home else "")
        venue.set("date", _venue_date(game.date or "") or (ev.get("date") or ""))
        venue.set("location", game.location or ev.get("location") or "")
        venue.set("stadium", game.stadium or "")
        venue.set("start", game.start_time or ev.get("timeStart") or "")
        venue.set("end", ev.get("timeEnd") or "")
        venue.set("duration", game.duration or ev.get("duration") or "")
        venue.set("delay", game.delayed_time or ev.get("delayDuration") or "")
        venue.set("attend", str(game.attendance or ev.get("attendance") or 0))
        venue.set("schednote", "")
        venue.set("leaguegame", "Y" if game.is_league_game else "N")
        venue.set("neutralgame", "Y" if game.is_neutral else "N")
        venue.set("postseason", "Y" if (ev.get("postseason") or False) else "N")

        officials = ET.SubElement(venue, "officials")
        refs = ev.get("referees") or ["", "", "", "", "", "", "", "", ""]
        # Order per Presto: ref ump line lj bj fj sj sc cj
        keys = ["ref", "ump", "line", "lj", "bj", "fj", "sj", "sc", "cj"]
        for i, k in enumerate(keys):
            officials.set(k, str(refs[i]) if i < len(refs) and refs[i] else "")

        rules = ET.SubElement(venue, "rules")
        rules.set("qtrs", str(ev.get("rulesPeriods") or 4))
        rules.set("mins", str(ev.get("minutesPrd") or 15))
        rules.set("downs", str(ev.get("downs") or 4))
        rules.set("yds", str(ev.get("firstDownYards") or 10))
        rules.set("kospot", str(ev.get("kickoffSpot") or 35))
        rules.set("kotbspot", str(ev.get("otherTouchBack") or 25))
        rules.set("tbspot", str(ev.get("touchBackSpot") or 20))
        rules.set("patspot", str(ev.get("patTrySpot") or 3))
        rules.set("safspot", str(ev.get("safetySpot") or 20))
        rules.set("td", str(ev.get("touchDown") or 6))
        rules.set("fg", str(ev.get("fieldGoal") or 3))
        rules.set("pat", str(ev.get("kickPat") or 1))
        rules.set("patx", str(ev.get("otherPat") or 2))
        rules.set("saf", str(ev.get("safety") or 2))
        rules.set("defpat", str(ev.get("defPat") or 2))
        rules.set("rouge", str(ev.get("rouge") or 1))
        rules.set("field", str(ev.get("fieldLength") or 100))
        rules.set("toh", str(ev.get("toHalf") or 3))
        rules.set("sackrush", "Y" if ev.get("sackRush") else "N")
        rules.set("fgaplay", "Y" if ev.get("fgaDrvPlay") else "N")
        rules.set("netpunttb", "Y")

    def _write_status(self, root, ev):
        status = ET.SubElement(root, "status")
        try:
            sc = int(ev.get("statusCode") or 0)
        except (TypeError, ValueError):
            sc = 2 if str(ev.get("status") or "").lower().startswith("final") else 0
        complete = sc >= 2
        status.set("complete", "Y" if complete else "N")
        status.set("running", "F")
        try:
            period = int(ev.get("statusPeriod") or 0)
        except (TypeError, ValueError):
            period = 0
        status.set("period", str(period))
        def _toi(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return 0
        mins = _toi(ev.get("currentMinute") or ev.get("statusMinutes") or 0)
        secs = _toi(ev.get("currentSecond") or ev.get("statusSeconds") or 0)
        status.set("clock", f"{mins:02d}:{secs:02d}")

    def _write_team(self, root, vh, team, team_blob, opp_blob, vis_id, home_id, blob):
        if not team_blob:
            return
        t_el = ET.SubElement(root, "team")
        t_el.set("vh", vh)
        t_el.set("code", team_blob.get("name") or (team.name if team else ""))
        t_el.set("id", team_blob.get("abbr") or _ext_id(team))
        t_el.set("name", team_blob.get("name") or (team.name if team else ""))
        t_el.set("record", team_blob.get("record") or "0-0")
        t_el.set("conf-record", team_blob.get("record_conf") or "0")
        t_el.set("abb", team_blob.get("keyStroke") or (team.abbreviation if team else ""))

        # Linescore
        prd_scores = [int((p.get("score") or 0)) for p in (team_blob.get("periodstats") or [])]
        if not prd_scores:
            prd_scores = [0, 0, 0, 0]
        ln = ET.SubElement(t_el, "linescore")
        ln.set("prds", str(len(prd_scores)))
        ln.set("line", ",".join(str(s) for s in prd_scores))
        ln.set("score", str(sum(prd_scores)))
        for i, s in enumerate(prd_scores, start=1):
            lp = ET.SubElement(ln, "lineprd")
            lp.set("prd", str(i))
            lp.set("score", str(s))

        # Team totals + sub-blocks
        totals = ET.SubElement(t_el, "totals")
        tot_off_plays = team_blob.get("totalOffPlays")
        tot_off_yards = team_blob.get("totalOffYards")
        if tot_off_plays:
            totals.set("totoff_plays", str(tot_off_plays))
        if tot_off_yards:
            totals.set("totoff_yards", str(tot_off_yards))
        if tot_off_plays:
            try:
                avg = round((tot_off_yards or 0) / tot_off_plays, 1)
                totals.set("totoff_avg", str(avg))
            except ZeroDivisionError:
                pass

        for tag in ("firstdowns", "penalties", "conversions", "fumbles"):
            _emit_block(totals, tag, team_blob, _BLOCKS[tag])
        # misc with TOP (mm:ss)
        misc_yds = team_blob.get("miscYards") or 0
        top_secs = team_blob.get("miscPossession") or 0
        if misc_yds or top_secs:
            misc = ET.SubElement(totals, "misc")
            top_mm = top_secs // 60 if top_secs else 0
            top_ss = top_secs % 60 if top_secs else 0
            misc.set("top", f"{top_mm:02d}:{top_ss:02d}")
            misc.set("ona", "0")
            misc.set("onm", "0")
            misc.set("yds", str(misc_yds))
        _emit_block(totals, "redzone", team_blob, _BLOCKS["redzone"])
        for tag in ("rush", "pass", "rcv", "punt", "ko", "fg", "pat", "defense", "kr", "pr", "fr", "ir"):
            _emit_block(totals, tag, team_blob, _BLOCKS[tag])

        # Team scoring summary (td/patkick/...) — derived from <scoring> events
        ev = blob.get("eventInfo") or {}
        scoring_evs = ev.get("scoring") or []
        is_visitor = vh == "V"
        s_tot = {"td": 0, "fg": 0, "saf": 0, "patkick": 0, "patrcv": 0, "patrush": 0, "patpass": 0}
        for s in scoring_evs:
            if bool(s.get("homeTeam")) != (not is_visitor):
                continue
            stype = (s.get("type") or "").upper()
            if stype == "TD":
                s_tot["td"] += 1
            elif stype == "FG":
                s_tot["fg"] += 1
            elif stype == "SAF":
                s_tot["saf"] += 1
            kind = self._pat_kind_from_code(s.get("patCode"))
            if (s.get("patres") or "").upper() == "GOOD" or kind:
                if kind == "kick":
                    s_tot["patkick"] += 1
                elif kind == "rush":
                    s_tot["patrush"] += 1
                elif kind == "pass":
                    s_tot["patpass"] += 1
        if any(v for v in s_tot.values()):
            sc = ET.SubElement(totals, "scoring")
            for k, v in s_tot.items():
                if v:
                    sc.set(k, str(v))

        # Per-player stat lines (only players with stats or 'gp')
        for p in team_blob.get("players", []):
            pe = ET.SubElement(t_el, "player")
            uni = str(p.get("uniform") or "")
            name = (p.get("completeName") or "").strip()
            pe.set("uni", uni)
            pe.set("name", name)
            pe.set("checkname", _checkname(name))
            pe.set("shortname", _short_name(name))
            pe.set("gp", "1" if p.get("participated") else "0")
            pe.set("code", uni)
            pe.set("playerId", p.get("playerId") or "")
            for tag in _PLAYER_BLOCK_ORDER:
                if tag == "fumbles":
                    if (p.get("fumblesNo") or 0):
                        fb = ET.SubElement(pe, "fumbles")
                        fb.set("no", str(p.get("fumblesNo") or 0))
                        fb.set("lost", str(p.get("fumblesLost") or 0))
                    continue
                _emit_block(pe, tag, p, _BLOCKS[tag])
            # Per-player scoring tally (used by Presto reports)
            score_attrs: Dict[str, int] = {}
            td_total = (p.get("rushTd", 0) or 0) + (p.get("passTd", 0) or 0) + \
                       (p.get("receivingTd", 0) or 0) + (p.get("returnsKickTd", 0) or 0) + \
                       (p.get("returnsPuntTd", 0) or 0) + (p.get("returnsIntTd", 0) or 0) + \
                       (p.get("returnsFumbTd", 0) or 0)
            if td_total:
                score_attrs["td"] = td_total
            if p.get("epOffKickMd"):
                score_attrs["patkick"] = p["epOffKickMd"]
            if p.get("epOffPassMd"):
                score_attrs["patpass"] = p["epOffPassMd"]
            if p.get("epOffRushMd"):
                score_attrs["patrush"] = p["epOffRushMd"]
            if p.get("epOffRcvMd"):
                score_attrs["patrcv"] = p["epOffRcvMd"]
            if p.get("kickFgMad"):
                score_attrs["fg"] = p["kickFgMad"]
            if score_attrs:
                sc = ET.SubElement(pe, "scoring")
                for k, v in score_attrs.items():
                    sc.set(k, str(v))

    def _write_scores(self, root, ev, vis_blob, home_blob, vis_id, home_id):
        scoring = ev.get("scoring") or []
        if not scoring:
            return
        scores = ET.SubElement(root, "scores")
        for s in scoring:
            sc = ET.SubElement(scores, "score")
            home_team = bool(s.get("homeTeam"))
            team_blob = home_blob if home_team else vis_blob
            scorer = self._player_label(team_blob, str(s.get("scorer") or ""))
            passer = self._player_label(team_blob, str(s.get("passer") or "")) if s.get("passer") else ""
            patby  = self._player_label(team_blob, str(s.get("patBy") or "")) if s.get("patBy") else ""
            mins = int(s.get("mins") or 0); secs = int(s.get("secs") or 0)
            sc.set("how", (s.get("how") or "").upper())
            sc.set("patby", patby)
            sc.set("qtr", str(s.get("quarter") or 0))
            sc.set("team", team_blob.get("abbr") or (home_id if home_team else vis_id))
            sc.set("scorer", scorer)
            if passer:
                sc.set("passer", passer)
            sc.set("vh", "H" if home_team else "V")
            sc.set("type", (s.get("type") or "").upper())
            sc.set("clock", f"{mins:02d}:{secs:02d}")
            sc.set("driveindex", str(s.get("driveIdx") or 0))
            sc.set("hscore", str(s.get("homeScore") or 0))
            sc.set("vscore", str(s.get("visitorScore") or 0))
            if s.get("yards") is not None:
                sc.set("yds", str(s.get("yards")))
            patres = (s.get("patres") or "").upper()
            patcode = s.get("patCode")
            kind = self._pat_kind_from_code(patcode)
            if patres:
                sc.set("patres", patres)
            elif kind:
                sc.set("patres", "GOOD")
            if kind:
                sc.set("pattype", kind.upper())

    def _write_fgas(self, root, ev, vis_blob, home_blob, vis_id, home_id):
        goals = ev.get("goals") or []
        if not goals:
            return
        fgas = ET.SubElement(root, "fgas")
        for g in goals:
            fga = ET.SubElement(fgas, "fga")
            home_team = bool(g.get("homeTeam"))
            team_blob = home_blob if home_team else vis_blob
            kicker = self._player_label(team_blob, str(g.get("uniform") or ""))
            secs = int(g.get("secs") or 0)
            clock = f"{secs // 60:02d}:{secs % 60:02d}"
            fga.set("distance", str(g.get("distance") or 0))
            fga.set("clock", clock)
            fga.set("qtr", str(g.get("quarter") or 0))
            res = (g.get("result") or "").upper()
            fga.set("result", "good" if res in ("G", "GOOD") else ("blocked" if res in ("B", "BLK") else "no good"))
            fga.set("team", team_blob.get("abbr") or (home_id if home_team else vis_id))
            fga.set("vh", "H" if home_team else "V")
            fga.set("kicker", kicker)

    def _write_drives(self, root, ev, vis_id, home_id):
        drives = ev.get("drives") or []
        if not drives:
            return
        d_el = ET.SubElement(root, "drives")
        for i, d in enumerate(drives, start=1):
            de = ET.SubElement(d_el, "drive")
            de.set("driveindex", str(i))
            home_team = bool(d.get("homeTeam"))
            tid = home_id if home_team else vis_id
            de.set("vh", "H" if home_team else "V")
            de.set("yards", str(d.get("yards") or 0))
            de.set("top", _format_top(d.get("topSecs") or 0))
            de.set("end_how", d.get("endHow") or "")
            de.set("end_qtr", str(d.get("endQuarter") or 0))
            esecs = int(d.get("endSecs") or 0)
            de.set("end_time", f"{esecs // 60:02d}:{esecs % 60:02d}")
            de.set("end_spot", f"STATS{tid}{int(d.get('endSpot') or 0):02d}")
            de.set("start_how", d.get("startHow") or "")
            de.set("start_qtr", str(d.get("startQuarter") or 0))
            ssecs = int(d.get("startSecs") or 0)
            de.set("start_time", f"{ssecs // 60:02d}:{ssecs % 60:02d}")
            de.set("start_spot", f"STATS{tid}{int(d.get('startSpot') or 0):02d}")
            de.set("plays", str(d.get("playsSum") or 0))
            de.set("team", tid)
            if d.get("redZone"):
                de.set("rz", "1")

    def _write_plays(self, root, blob, vis_id, home_id, vis_blob, home_blob):
        """Emit ``<plays format='summary'>`` with one ``<qtr>`` per period.

        We use the decoded narrative (best-effort) so the XML round-trips
        readably; deeper p_* sub-elements are added when easily derivable from
        the RAW_PLAY tokens."""
        plays_dict = blob.get("plays") or {}
        if not plays_dict:
            return
        plays_el = ET.SubElement(root, "plays")
        plays_el.set("format", "summary")
        for prd_key in sorted(plays_dict.keys(), key=lambda k: int(k)):
            qtr_el = ET.SubElement(plays_el, "qtr")
            qtr_el.set("number", prd_key)
            qtr_el.set("text", _ord(int(prd_key)))
            for play in plays_dict[prd_key]:
                props = play.get("props") or {}
                raw = props.get("RAW_PLAY") or ""
                pos_is_home = _possession_is_home(props, play)
                pos = home_blob if pos_is_home else vis_blob
                deff = vis_blob if pos_is_home else home_blob
                text = decode_play(
                    raw,
                    possession_team=pos, defense_team=deff,
                    vis_id=vis_id, home_id=home_id,
                    vis_blob=vis_blob, home_blob=home_blob,
                )
                if text is None:
                    text = props.get("COMMENT") or raw
                if not text:
                    continue
                pe = ET.SubElement(qtr_el, "play")
                pe.set("hasball", (home_blob.get("abbr") if pos_is_home else vis_blob.get("abbr")) or "")
                pe.set("text", text)
                pe.set("clock", props.get("CLOCK") or "")
                pe.set("playid", play.get("id") or "")
            ET.SubElement(qtr_el, "endqtr")


def _ord(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _possession_is_home(props: Dict[str, Any], play: Dict[str, Any]) -> bool:
    """Decide which team has the ball for a play.

    The play record's ``homeTeam`` flag tracks the side that entered the play
    in GWT, not who actually had possession (e.g. a defensive tackle ends up
    flagged as the defending side). The reliable signal is the third comma-
    separated field of ``INITIAL_CONTEXT`` (``V`` or ``H``)."""
    ctx = props.get("INITIAL_CONTEXT") or props.get("UPDATED_CONTEXT") or ""
    if ctx:
        parts = ctx.split(",")
        if len(parts) >= 3:
            side = (parts[2] or "").strip().upper()
            if side == "H":
                return True
            if side == "V":
                return False
    return bool(play.get("homeTeam"))
