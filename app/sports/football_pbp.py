"""
Football play-by-play decoder.

Translates the compact RAW_PLAY notation stored in the GWT boxscore blob
into Presto-style human-readable narrative ("Roberts rush for 5 yards to the
STATS243 (Narcesca Arzu)") and the structured sub-elements used in the
``<plays format='summary'>`` block of the Presto fbgame XML.

The grammar is line-oriented; each token is `KEY:VAL` separated by spaces:

    RUSH:<uni>,<spot>             TACK:<uni>[,<uni>...]   OB:
    PASS:<qb>,<C|I>,<rcv>[,<spot>] TACK:<uni>...           OB:
    PASS:<qb>,<S>,<rcv>,<sackyds>  (sack)
    KO:<uni>,<spot>               RET:<uni>,<spot>         TACK:<uni>
    PUNT:<uni>,<spot>             RET:<uni>,<spot>         TB:
    FG:<uni>,<dist>,<G|N|B>
    PAT:K|R|P,<G|N>,<uni>[,<rcv>]
    PEN:V|H,<code>,A|D,<uni>,<spot>,Y|N
    INT:<uni>,<spot>
    FUMB:<uni>,<spot>             REC:<uni>,<spot>
    SACK:<qb>,<uni>,<yds>
    {DRIVE}:<clock>
    SPOT:<vh>,<spot>,...          T:<clock>
    Q:<num> T:<clock>             — quarter marker

Spots use Presto convention: ``V35`` = visitor's 35, ``H20`` = home's 20.
``STATSn35`` becomes ``STATS<team-id>35`` in the rendered text (matches the
sample XML).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _player_name(team_blob: Dict[str, Any], uni: str,
                 alt_team_blob: Optional[Dict[str, Any]] = None) -> str:
    """Return the player's display name (or 'Team' for TM, or uniform itself).

    The play's ``homeTeam`` flag in the GWT blob does not always correspond to
    possession, so we fall back to the other team's roster when ``uni`` isn't
    found in the primary team. This produces correct names for both teams in
    rush/pass/tackle/etc. lookups."""
    if not uni:
        return ""
    if str(uni).upper() in ("TM", "TEAM"):
        return "Team"
    for p in (team_blob or {}).get("players", []):
        if str(p.get("uniform") or "") == str(uni):
            return (p.get("completeName") or p.get("name") or f"#{uni}").strip() or f"#{uni}"
    if alt_team_blob:
        for p in alt_team_blob.get("players", []):
            if str(p.get("uniform") or "") == str(uni):
                return (p.get("completeName") or p.get("name") or f"#{uni}").strip() or f"#{uni}"
    return f"#{uni}"


_STATS_PREFIX_RE = re.compile(r"^STATS", re.I)


def _team_short(tid: str) -> str:
    """Strip any 'STATS' prefix so 'STATS1' becomes '1' for use in 'STATS1<nn>'."""
    if not tid:
        return ""
    return _STATS_PREFIX_RE.sub("", tid)


def _spot_text(spot: str, vis_id: str, home_id: str) -> str:
    """Convert 'V35' or 'H20' into 'STATS135' style display text."""
    if not spot:
        return ""
    spot = spot.strip().upper()
    vshort = _team_short(vis_id) or "1"
    hshort = _team_short(home_id) or "2"
    if spot in ("V00", "H00"):
        return f"STATS{vshort if spot.startswith('V') else hshort}00"
    m = re.match(r"^([VH])(\d{1,2})$", spot)
    if not m:
        return spot
    side, num = m.group(1), m.group(2)
    tid = vshort if side == "V" else hshort
    num = num.zfill(2)
    if num == "50":
        return "50 yardline"
    return f"STATS{tid}{num}"


def _tokenise(raw: str) -> List[Tuple[str, str]]:
    """Split 'RUSH:22,V32 TACK:21 OB:' into [('RUSH','22,V32'), ('TACK','21'), ('OB','')]."""
    if not raw:
        return []
    out: List[Tuple[str, str]] = []
    # Match KEY:value-without-spaces but allow {DRIVE} braces and commas
    for tok in re.findall(r"(\{?[A-Z_][A-Z0-9_]*\}?):([^\s]*)", raw):
        out.append((tok[0], tok[1]))
    return out


def _format_tackles(tack: str, defense_team_blob: Dict[str, Any],
                    alt_team_blob: Optional[Dict[str, Any]] = None) -> str:
    if not tack:
        return ""
    parts = [t for t in tack.split(",") if t]
    names = [_player_name(defense_team_blob, t, alt_team_blob) for t in parts]
    if not names:
        return ""
    if len(names) == 1:
        return f"({names[0]})"
    return "(" + "; ".join(names) + ")"


def _yards_phrase(n: int, unit: str = "yard") -> str:
    n = int(n)
    if n == 0:
        return f"no gain"
    if n < 0:
        return f"loss of {abs(n)} {unit}{'s' if abs(n) != 1 else ''}"
    return f"{n} {unit}{'s' if n != 1 else ''}"


_PENALTY_CODES: Dict[str, str] = {
    "FS":  "False Start",
    "OS":  "Offsides",
    "HTF": "Hands to the face",
    "FM":  "Face Mask",
    "PI":  "Pass Interference",
    "DPI": "Defense Pass Interference",
    "OPI": "Offense Pass Interference",
    "HC":  "Horse Collar Tackle",
    "HOLD":"Holding",
    "DH":  "Defensive Holding",
    "OH":  "Offensive Holding",
    "RTP": "Roughing the Passer",
    "RTK": "Roughing the Kicker",
    "UC":  "Unsportsmanlike Conduct",
    "PF":  "Personal Foul",
    "DG":  "Delay of Game",
    "ENC": "Encroachment",
    "ILL": "Illegal Procedure",
    "TGT": "Targeting",
    "ILP": "Illegal Participation",
    "IBC": "Illegal Block in the Back",
    "ILS": "Illegal Shift",
    "ILM": "Illegal Motion",
}


def decode_play(raw: str, *, possession_team: Dict[str, Any], defense_team: Dict[str, Any],
                vis_id: str, home_id: str, vis_blob: Dict[str, Any],
                home_blob: Dict[str, Any]) -> Optional[str]:
    """Return the narrative string for one RAW_PLAY, or None if it's a marker
    (drive start, quarter start, spot, etc. — handled separately).

    The GWT play's ``homeTeam`` flag isn't reliable possession info, so we
    pass both team rosters to all player-name lookups and let them fall back
    to the other side when a uniform isn't found in the primary team."""
    if not raw:
        return None
    raw = raw.strip()

    # Markers: quarter / drive start / spot / clock-only → no narrative
    if raw.startswith("Q:") or raw.startswith("{DRIVE}") or raw.startswith("SPOT:"):
        return ""
    if raw.startswith("T:") and ":" in raw and len(raw) <= 12 and "RUSH" not in raw and "PASS" not in raw:
        return ""

    toks = dict(_tokenise(raw))
    # RUSH:<uni>,<spot>
    if "RUSH" in toks:
        val = toks["RUSH"]
        m = re.match(r"^(\w+),([VH]\d{1,2})$", val)
        if m:
            uni, spot = m.group(1), m.group(2)
            name = _player_name(possession_team, uni, defense_team)
            sp = _spot_text(spot, vis_id, home_id)
            tk = _format_tackles(toks.get("TACK", ""), defense_team, possession_team)
            ob = ", out-of-bounds" if "OB" in toks else ""
            base = f"{name} rush to the {sp}"
            if tk:
                base += f" {tk}"
            return base + ob
    # PASS:<qb>,<C|I|S>,<rcv>[,<spot or sackyds>]
    if "PASS" in toks:
        val = toks["PASS"]
        parts = val.split(",")
        qb = parts[0] if parts else ""
        result = parts[1] if len(parts) > 1 else ""
        rcv = parts[2] if len(parts) > 2 else ""
        spot_or_yd = parts[3] if len(parts) > 3 else ""
        qb_name = _player_name(possession_team, qb, defense_team)
        rcv_name = _player_name(possession_team, rcv, defense_team)
        tk = _format_tackles(toks.get("TACK", ""), defense_team, possession_team)
        ob = ", out-of-bounds" if "OB" in toks else ""
        if result == "C":
            sp = _spot_text(spot_or_yd, vis_id, home_id) if spot_or_yd else ""
            msg = f"{qb_name} pass complete to {rcv_name}"
            if sp:
                msg += f" to the {sp}"
            if tk:
                msg += f" {tk}"
            return msg + ob
        if result == "I":
            return f"{qb_name} pass incomplete to {rcv_name}"
        if result == "S":
            return f"{qb_name} sacked for loss of {spot_or_yd} yards {tk}".strip()
        return f"{qb_name} pass to {rcv_name}"
    # KO:<uni>,<spot>  RET:<uni>,<spot>
    if "KO" in toks:
        kuni, kspot = (toks["KO"].split(",") + [""])[:2]
        kname = _player_name(possession_team, kuni, defense_team)
        ksp = _spot_text(kspot, vis_id, home_id)
        msg = f"{kname} kickoff to the {ksp}"
        if "RET" in toks:
            r = toks["RET"].split(",")
            runi = r[0] if r else ""
            rspot = r[1] if len(r) > 1 else ""
            rname = _player_name(defense_team, runi, possession_team)
            rsp = _spot_text(rspot, vis_id, home_id)
            msg += f", {rname} return to the {rsp}"
        tk = _format_tackles(toks.get("TACK", ""), possession_team, defense_team)
        if tk:
            msg += f" {tk}"
        if "TB" in toks:
            msg += ", touchback"
        if "OB" in toks:
            msg += ", out-of-bounds"
        return msg
    # PUNT
    if "PUNT" in toks:
        puni, pspot = (toks["PUNT"].split(",") + [""])[:2]
        pname = _player_name(possession_team, puni, defense_team)
        psp = _spot_text(pspot, vis_id, home_id)
        msg = f"{pname} punt to the {psp}"
        if "RET" in toks:
            r = toks["RET"].split(",")
            runi = r[0] if r else ""
            rspot = r[1] if len(r) > 1 else ""
            rname = _player_name(defense_team, runi, possession_team)
            rsp = _spot_text(rspot, vis_id, home_id)
            msg += f", {rname} return to the {rsp}"
        tk = _format_tackles(toks.get("TACK", ""), possession_team, defense_team)
        if tk:
            msg += f" {tk}"
        if "TB" in toks:
            msg += ", touchback"
        if "OB" in toks:
            msg += ", out-of-bounds"
        return msg
    # Field goal: FG:<uni>,<dist>,<G|N|B>
    if "FG" in toks:
        parts = toks["FG"].split(",")
        uni = parts[0] if parts else ""
        dist = parts[1] if len(parts) > 1 else ""
        res  = parts[2] if len(parts) > 2 else ""
        name = _player_name(possession_team, uni, defense_team)
        gd = {"G": "good", "N": "no good", "B": "blocked"}.get(res, res or "")
        return f"{name} field goal attempt from {dist} {gd}".strip()
    # PAT
    if "PAT" in toks:
        parts = toks["PAT"].split(",")
        kind = parts[0] if parts else ""
        result = parts[1] if len(parts) > 1 else ""
        uni = parts[2] if len(parts) > 2 else ""
        rcv = parts[3] if len(parts) > 3 else ""
        name = _player_name(possession_team, uni, defense_team)
        rname = _player_name(possession_team, rcv, defense_team) if rcv else ""
        if kind == "K":
            verb = "kick attempt"
        elif kind == "P":
            verb = f"pass attempt to {rname}" if rname else "pass attempt"
        elif kind == "R":
            verb = "rush attempt"
        else:
            verb = "PAT"
        gd = "good" if result == "G" else ("failed" if result in ("N", "F") else (result or ""))
        return f"{name} {verb} {gd}".strip()
    # SACK alt form
    if "SACK" in toks:
        parts = toks["SACK"].split(",")
        qb = parts[0] if parts else ""
        sacker = parts[1] if len(parts) > 1 else ""
        yds = parts[2] if len(parts) > 2 else ""
        qb_name = _player_name(possession_team, qb, defense_team)
        s_name = _player_name(defense_team, sacker, possession_team)
        return f"{qb_name} sacked by {s_name} for loss of {yds} yards"
    # Interception
    if "INT" in toks:
        parts = toks["INT"].split(",")
        uni = parts[0] if parts else ""
        spot = parts[1] if len(parts) > 1 else ""
        name = _player_name(defense_team, uni, possession_team)
        sp = _spot_text(spot, vis_id, home_id)
        return f"intercepted by {name} at the {sp}"
    # Fumble
    if "FUMB" in toks:
        parts = toks["FUMB"].split(",")
        uni = parts[0] if parts else ""
        spot = parts[1] if len(parts) > 1 else ""
        name = _player_name(possession_team, uni, defense_team)
        sp = _spot_text(spot, vis_id, home_id)
        rec_txt = ""
        if "REC" in toks:
            rp = toks["REC"].split(",")
            r_uni = rp[0] if rp else ""
            r_spot = rp[1] if len(rp) > 1 else ""
            r_name = _player_name(defense_team, r_uni, possession_team)
            r_sp = _spot_text(r_spot, vis_id, home_id)
            rec_txt = f", recovered by {r_name} at the {r_sp}"
        return f"fumble by {name} at the {sp}{rec_txt}"
    # Penalty: PEN:V|H,<code>,A|D,<uni>,<spot>,Y|N
    if "PEN" in toks:
        parts = toks["PEN"].split(",")
        side = parts[0] if parts else ""
        code = parts[1] if len(parts) > 1 else ""
        result = parts[2] if len(parts) > 2 else ""
        on_uni = parts[3] if len(parts) > 3 else ""
        spot = parts[4] if len(parts) > 4 else ""
        on_team = vis_blob if side == "V" else home_blob
        on_name = _player_name(on_team, on_uni, home_blob if side == "V" else vis_blob)
        desc = _PENALTY_CODES.get(code, code)
        team_label = (vis_blob.get("name") or "Visitor") if side == "V" else (home_blob.get("name") or "Home")
        sp = _spot_text(spot, vis_id, home_id)
        accepted = "accepted" if result == "A" else ("declined" if result == "D" else result)
        sub = f"({on_name})" if on_name and on_name != "Team" else ""
        out = f"PENALTY {team_label} {desc}"
        if sub:
            out += f" {sub}"
        out += f" {accepted} at the {sp}"
        return re.sub(r"\s+", " ", out).strip()
    # Timeout: TO:<vh>,<clock>
    if "TO" in toks:
        parts = toks["TO"].split(",")
        side = parts[0] if parts else ""
        clock = parts[1] if len(parts) > 1 else ""
        team_name = vis_blob.get("name") if side == "V" else home_blob.get("name")
        return f"Time out by {team_name}, clock {clock}"

    return None
