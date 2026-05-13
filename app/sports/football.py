"""
Football Sport handler (sport_id=0).

Everything the GWT app knows about a football game is in `Game.gwt_bs_blob`
(the JSON payload that mirrors Presto's `boxscore_*.json`). We parse it on
demand and produce all four output formats:

    JSON  — `render_json()`        the blob verbatim (already Presto-shaped)
    XML   — `build_xml()`          Presto `<fbgame>` v7.16 document
    HTML  — `render_html()`        live in-app boxscore page
    PDF   — `render_pdf(style=…)`  one of three print-friendly layouts:
                style='full'       full multi-page boxscore  (~15 pages)
                style='summary'    compact 1-2 page boxscore
                style='pbp'        play-by-play (one quarter at a time, via ?qtr=)

We persist only the blob + a quarter-by-quarter line score (so the schedule
page can display "35-17 Final"). Per-stat SQL tables aren't needed because
the blob is the single source of truth.
"""
from __future__ import annotations

import json as _json
import re
import xml.etree.ElementTree as ET
from datetime import date as _date

from flask import render_template, request

from app.sports.base import Sport


# Map GWT patCode → (pattype, patres) for Presto XML <score>.
# Codes inferred from the cross-referenced sample XML/JSON pair.
_PAT_CODE_MAP = {
    16: ('KICK', 'GOOD'),
    17: ('KICK', 'MISSED'),
    18: ('PASS', 'GOOD'),
    19: ('PASS', 'FAIL'),
    32: ('RUSH', 'GOOD'),
    33: ('RUSH', 'FAIL'),
    34: ('PASS', 'FAIL'),
    35: ('RCV',  'GOOD'),
}


# ── small helpers ────────────────────────────────────────────────────────

def _int(v, default=0):
    try:
        return int(v) if v not in (None, '', False, True) or v == 0 else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default=0):
    """int() that treats None/blank as default. Accepts strings like '34'."""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _div(numer, denom, digits=1):
    if not denom:
        return '0.0' if digits else '0'
    return f'{(numer / denom):.{digits}f}'


def _mmss(total_secs):
    total_secs = max(0, int(total_secs or 0))
    return f'{total_secs // 60:02d}:{total_secs % 60:02d}'


def _date_us(iso):
    """YYYY-MM-DD → M/D/YYYY (Presto format)."""
    if not iso:
        return ''
    try:
        y, m, d = iso.split('-')
        return f'{int(m)}/{int(d)}/{y}'
    except Exception:
        return iso


def _avg_thousandths(stored):
    """GWT stores per-attempt averages multiplied by 1000 (rushAvg, receivingAvg).
       Punt avg is multiplied by 100 in some builds — we autoscale."""
    if not stored:
        return 0.0
    try:
        v = float(stored)
    except (TypeError, ValueError):
        return 0.0
    if v >= 100:
        return v / 1000.0
    return v


def _punt_avg(stored):
    if not stored:
        return 0.0
    try:
        v = float(stored)
    except (TypeError, ValueError):
        return 0.0
    # Sample: 3400 → 34.0; some payloads may already be the actual avg.
    return v / 100.0 if v >= 1000 else v


def _ord(n):
    n = int(n)
    if 11 <= (n % 100) <= 13:
        return f'{n}th'
    return f'{n}' + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def _qtr_label(n):
    return {1: '1st', 2: '2nd', 3: '3rd', 4: '4th', 5: 'OT'}.get(int(n or 0), f'{int(n or 0)}th')


# ── blob loader ──────────────────────────────────────────────────────────

def _load_blob(game):
    """Return the GWT boxscore blob as a dict (never None)."""
    raw = (game.gwt_bs_blob or '').strip()
    if not raw:
        return _empty_blob(game)
    try:
        return _json.loads(raw)
    except (ValueError, TypeError):
        return _empty_blob(game)


def _empty_blob(game):
    """Build a minimal blob shell when no GWT data has been saved yet."""
    return {
        'teams': [
            {'name': (game.visitor_team.name if game.visitor_team else ''),
             'customizedName': (game.visitor_team.name if game.visitor_team else ''),
             'abbr': (game.visitor_team.abbreviation or game.visitor_team.code) if game.visitor_team else 'VIS',
             'record': '0-0', 'players': [], 'periodstats': []},
            {'name': (game.home_team.name if game.home_team else ''),
             'customizedName': (game.home_team.name if game.home_team else ''),
             'abbr': (game.home_team.abbreviation or game.home_team.code) if game.home_team else 'HOM',
             'record': '0-0', 'players': [], 'periodstats': []},
        ],
        'eventInfo': {
            'date': _date_us(game.date),
            'location': game.location or '',
            'gamePeriods': game.scheduled_innings or 4,
            'minutesPrd': 15,
            'scoring': [],
            'drives': [],
            'sportCode': 'fball',
            'startingTeam': 'V',
            'referees': ['', '', '', '', '', '', '', game.scorer or '', ''],
        },
        'plays': [''] * (game.scheduled_innings or 4),
        'countPeriods': game.scheduled_innings or 4,
        'gamePeriods': game.scheduled_innings or 4,
    }


# ── normalized boxscore for templates / JSON ─────────────────────────────

def boxscore_data(game):
    """Build a sport-aware boxscore dict suitable for HTML/PDF/JSON.

    The shape is football-specific (no innings/batting/pitching). All player
    rows are filtered to participants only and sorted into the position-group
    tables that the PDF/HTML layouts use.
    """
    blob = _load_blob(game)
    ev = blob.get('eventInfo') or {}
    teams = blob.get('teams') or [{}, {}]
    if len(teams) < 2:
        teams = teams + [{}] * (2 - len(teams))

    vis_team = teams[0] or {}
    home_team = teams[1] or {}
    starting_visitor = (ev.get('startingTeam') or 'V').upper() == 'V'

    # Linescore: one cell per period from periodstats[].score.
    nperiods = max(len(vis_team.get('periodstats') or []),
                   len(home_team.get('periodstats') or []),
                   int(ev.get('gamePeriods') or 4))

    def _period_score(team, i):
        ps = team.get('periodstats') or []
        if i < len(ps):
            v = _safe_int(ps[i].get('score'), 0)
            return 'X' if v == 99 else v
        return 0

    linescore = []
    for i in range(nperiods):
        linescore.append({
            'period': i + 1,
            'label': _qtr_label(i + 1),
            'v': _period_score(vis_team, i),
            'h': _period_score(home_team, i),
        })

    def _team_score(team):
        total = 0
        for ps in (team.get('periodstats') or []):
            s = _safe_int(ps.get('score'), 0)
            if s != 99:
                total += s
        return total

    vis_score = _team_score(vis_team)
    home_score = _team_score(home_team)

    # Per-team totals (used in "TEAM SUMMARY" rows).
    def _team_totals(t):
        ra = _safe_int(t.get('rushAtt'))
        ry = _safe_int(t.get('rushYards'))
        rl = _safe_int(t.get('rushWinLoss'))  # negative
        pcomp = _safe_int(t.get('passComp'))
        patt = _safe_int(t.get('passAtt'))
        psacks = _safe_int(t.get('passSacks'))
        psy = _safe_int(t.get('passSackYards'))
        pty = _safe_int(t.get('passYards'))
        return {
            'first_downs': _safe_int(t.get('firstDownNo')),
            'fd_rush':    _safe_int(t.get('firstDownRush')),
            'fd_pass':    _safe_int(t.get('firstDownPass')),
            'fd_penalty': _safe_int(t.get('firstDownPenalty')),
            'rush_att':   ra,
            'rush_yds':   ry,
            'rush_loss':  abs(rl),
            'rush_gain':  ry - rl,
            'pass_att':   patt,
            'pass_comp':  pcomp,
            'pass_int':   _safe_int(t.get('passInt')),
            'pass_yds':   pty,
            'pass_td':    _safe_int(t.get('passTd')),
            'pass_sacks': psacks,
            'pass_sack_yds': psy,
            'plays':      _safe_int(t.get('totalOffPlays')),
            'total_yds':  _safe_int(t.get('totalOffYards')),
            'fumb_no':    _safe_int(t.get('fumblesNo')),
            'fumb_lost':  _safe_int(t.get('fumblesLost')),
            'pen_no':     _safe_int(t.get('penaltyNo')),
            'pen_yds':    _safe_int(t.get('penaltyYards')),
            'punt_no':    _safe_int(t.get('kickPuntNo')),
            'punt_yds':   _safe_int(t.get('kickPuntYards')),
            'punt_avg':   _punt_avg(t.get('kickPuntAvg')),
            'punt_long':  _safe_int(t.get('kickPuntLong')),
            'punt_tb':    _safe_int(t.get('kickPuntTb')),
            'punt_i20':   _safe_int(t.get('kickPuntI20')),
            'punt_50':    _safe_int(t.get('kickPunt50')),
            'ko_no':      _safe_int(t.get('kickKoNo')),
            'ko_yds':     _safe_int(t.get('kickKoYards')),
            'ko_tb':      _safe_int(t.get('kickKoTb')),
            'ko_ob':      _safe_int(t.get('kickKoOb')),
            'kr_no':      _safe_int(t.get('returnsKickNo')),
            'kr_yds':     _safe_int(t.get('returnsKickYards')),
            'kr_long':    _safe_int(t.get('returnsKickLong')),
            'kr_td':      _safe_int(t.get('returnsKickTd')),
            'pr_no':      _safe_int(t.get('returnsPuntNo')),
            'pr_yds':     _safe_int(t.get('returnsPuntYards')),
            'pr_long':    _safe_int(t.get('returnsPuntLong')),
            'pr_td':      _safe_int(t.get('returnsPuntTd')),
            'ir_no':      _safe_int(t.get('returnsIntNo')),
            'ir_yds':     _safe_int(t.get('returnsIntYards')),
            'ir_long':    _safe_int(t.get('returnsIntLong')),
            'ir_td':      _safe_int(t.get('returnsIntTd')),
            'fr_no':      _safe_int(t.get('returnsFumbNo')),
            'fr_yds':     _safe_int(t.get('returnsFumbYards')),
            'fr_long':    _safe_int(t.get('returnsFumbLong')),
            'fr_td':      _safe_int(t.get('returnsFumbTd')),
            'fg_made':    _safe_int(t.get('kickFgMad')),
            'fg_att':     _safe_int(t.get('kickFgAtt')),
            'fg_long':    _safe_int(t.get('kickFgLong')),
            'fg_blk':     _safe_int(t.get('kickFgBlk')),
            'pat_kick_md': _safe_int(t.get('epOffKickMd')),
            'pat_kick_at': _safe_int(t.get('epOffKickAt')),
            'pat_pass_md': _safe_int(t.get('epOffPassMd')),
            'pat_pass_at': _safe_int(t.get('epOffPassAt')),
            'pat_rush_md': _safe_int(t.get('epOffRushMd')),
            'pat_rush_at': _safe_int(t.get('epOffRushAt')),
            'pat_rcv_md':  _safe_int(t.get('epOffRcvMd')),
            'def_fumb_forc': _safe_int(t.get('defenseFumbForc')),
            'def_fumb_rcvr': _safe_int(t.get('defenseFumbRcvr')),
            'def_brup':      _safe_int(t.get('defensePassBrUp')),
            'def_sack_ua':   _safe_int(t.get('defenseSackUa')),
            'def_sack_a':    _safe_int(t.get('defenseSackA')),
            'def_sack_yds':  abs(_safe_int(t.get('defenseSackWinLossYards'))),
            'def_tack_ua':   _safe_int(t.get('defenseTackUa')),
            'def_tack_a':    _safe_int(t.get('defenseTackA')),
            'def_tfl_ua':    _safe_int(t.get('defenseTflUa')),
            'def_tfl_a':     _safe_int(t.get('defenseTflA')),
            'def_tfl_yds':   abs(_safe_int(t.get('defenseTflLossYards'))),
            'def_qbh':       _safe_int(t.get('defenseQbh')),
            'def_saf':       _safe_int(t.get('defenseSaf')),
            'def_blk':       _safe_int(t.get('defenseBlkdKick')),
            # Conversions
            'third_conv':   _safe_int(t.get('conversionThirdConv')),
            'third_att':    _safe_int(t.get('conversionThirdAtt')),
            'fourth_conv':  _safe_int(t.get('conversionFourthConv')),
            'fourth_att':   _safe_int(t.get('conversionFourthAtt')),
            'top_secs':     _safe_int(t.get('miscPossession')),
            'top':          _mmss(_safe_int(t.get('miscPossession'))),
            # Red zone
            'rz_att':       _safe_int(t.get('redZoneTimeInside20')),
            'rz_scores':    _safe_int(t.get('redZoneTimeScored')),
            'rz_points':    _safe_int(t.get('redZonePointsScored')),
            'rz_td_rush':   _safe_int(t.get('redZoneRushingTds')),
            'rz_td_pass':   _safe_int(t.get('redZonePassingTds')),
            'rz_fg':        _safe_int(t.get('redZoneFgsMade')),
            'rz_end_fga':   _safe_int(t.get('redZoneEndOnFga')),
            'rz_end_dn':    _safe_int(t.get('redZoneEndOnDowns')),
            'rz_end_int':   _safe_int(t.get('redZoneEndOnIntCpt')),
            'rz_end_fumb':  _safe_int(t.get('redZoneEndOnFumble')),
            'rz_end_half':  _safe_int(t.get('redZoneEndOfHalf')),
            'rz_end_game':  _safe_int(t.get('redZoneEndOfGame')),
        }

    def _participated(p):
        return bool(p.get('participated')) or any(
            _safe_int(p.get(k)) for k in
            ('rushAtt', 'passAtt', 'receivingNo', 'kickKoNo', 'kickPuntNo',
             'returnsKickNo', 'returnsPuntNo', 'returnsIntNo', 'returnsFumbNo',
             'defenseTackUa', 'defenseTackA', 'defenseTflUa', 'defenseTflA',
             'defensePassBrUp', 'defenseSackUa', 'defenseSackA', 'defenseQbh',
             'defenseFumbForc', 'defenseFumbRcvr', 'defenseSaf', 'defenseBlkdKick',
             'kickFgAtt', 'epOffKickAt', 'epOffPassAt', 'epOffRushAt'))

    def _player_view(p):
        ra = _safe_int(p.get('rushAtt'))
        ry = _safe_int(p.get('rushYards'))
        rl = _safe_int(p.get('rushWinLoss'))
        return {
            'uni':   p.get('uniform') or '',
            'name':  p.get('completeName') or '',
            'pos':   p.get('pos') or p.get('position') or '',
            'starter': bool(p.get('starter') or p.get('starterOff') or p.get('starterDef')),
            # rushing
            'rush_att':  ra,
            'rush_yds':  ry,
            'rush_long': _safe_int(p.get('rushLong')),
            'rush_loss': abs(rl),
            'rush_avg':  _avg_thousandths(p.get('rushAvg')) or (ry / ra if ra else 0.0),
            'rush_td':   _safe_int(p.get('rushTd')),
            # passing
            'pass_att':  _safe_int(p.get('passAtt')),
            'pass_comp': _safe_int(p.get('passComp')),
            'pass_yds':  _safe_int(p.get('passYards')),
            'pass_long': _safe_int(p.get('passLong')),
            'pass_td':   _safe_int(p.get('passTd')),
            'pass_int':  _safe_int(p.get('passInt')),
            'pass_sacks':_safe_int(p.get('passSacks')),
            'pass_sack_yds': _safe_int(p.get('passSackYards')),
            # receiving
            'rec_no':    _safe_int(p.get('receivingNo')),
            'rec_yds':   _safe_int(p.get('receivingYards')),
            'rec_long':  _safe_int(p.get('receivingLong')),
            'rec_td':    _safe_int(p.get('receivingTd')),
            'rec_avg':   _avg_thousandths(p.get('receivingAvg')) or (_safe_int(p.get('receivingYards')) / _safe_int(p.get('receivingNo'))) if _safe_int(p.get('receivingNo')) else 0.0,
            # kicking
            'fg_made':   _safe_int(p.get('kickFgMad')),
            'fg_att':    _safe_int(p.get('kickFgAtt')),
            'fg_long':   _safe_int(p.get('kickFgLong')),
            'fg_blk':    _safe_int(p.get('kickFgBlk')),
            'pat_kick_md': _safe_int(p.get('epOffKickMd')),
            'pat_kick_at': _safe_int(p.get('epOffKickAt')),
            'pat_pass_md': _safe_int(p.get('epOffPassMd')),
            'pat_pass_at': _safe_int(p.get('epOffPassAt')),
            'pat_rush_md': _safe_int(p.get('epOffRushMd')),
            'pat_rush_at': _safe_int(p.get('epOffRushAt')),
            'pat_rcv_md':  _safe_int(p.get('epOffRcvMd')),
            # punting
            'punt_no':   _safe_int(p.get('kickPuntNo')),
            'punt_yds':  _safe_int(p.get('kickPuntYards')),
            'punt_avg':  _punt_avg(p.get('kickPuntAvg')),
            'punt_long': _safe_int(p.get('kickPuntLong')),
            'punt_tb':   _safe_int(p.get('kickPuntTb')),
            'punt_i20':  _safe_int(p.get('kickPuntI20')),
            'punt_blkd': _safe_int(p.get('kickPuntBlkd')),
            'punt_50':   _safe_int(p.get('kickPunt50')),
            'punt_fc':   _safe_int(p.get('kickPuntFc')),
            # kickoffs
            'ko_no':     _safe_int(p.get('kickKoNo')),
            'ko_yds':    _safe_int(p.get('kickKoYards')),
            'ko_tb':     _safe_int(p.get('kickKoTb')),
            'ko_ob':     _safe_int(p.get('kickKoOb')),
            # returns
            'kr_no':     _safe_int(p.get('returnsKickNo')),
            'kr_yds':    _safe_int(p.get('returnsKickYards')),
            'kr_long':   _safe_int(p.get('returnsKickLong')),
            'kr_td':     _safe_int(p.get('returnsKickTd')),
            'pr_no':     _safe_int(p.get('returnsPuntNo')),
            'pr_yds':    _safe_int(p.get('returnsPuntYards')),
            'pr_long':   _safe_int(p.get('returnsPuntLong')),
            'pr_td':     _safe_int(p.get('returnsPuntTd')),
            'ir_no':     _safe_int(p.get('returnsIntNo')),
            'ir_yds':    _safe_int(p.get('returnsIntYards')),
            'ir_long':   _safe_int(p.get('returnsIntLong')),
            'ir_td':     _safe_int(p.get('returnsIntTd')),
            'fr_no':     _safe_int(p.get('returnsFumbNo')),
            'fr_yds':    _safe_int(p.get('returnsFumbYards')),
            'fr_long':   _safe_int(p.get('returnsFumbLong')),
            'fr_td':     _safe_int(p.get('returnsFumbTd')),
            # fumbles
            'fumb_no':   _safe_int(p.get('fumblesNo')),
            'fumb_lost': _safe_int(p.get('fumblesLost')),
            # defense
            'def_tack_ua': _safe_int(p.get('defenseTackUa')),
            'def_tack_a':  _safe_int(p.get('defenseTackA')),
            'def_tfl_ua':  _safe_int(p.get('defenseTflUa')),
            'def_tfl_a':   _safe_int(p.get('defenseTflA')),
            'def_tfl_yds': abs(_safe_int(p.get('defenseTflLossYards'))),
            'def_sack_ua': _safe_int(p.get('defenseSackUa')),
            'def_sack_a':  _safe_int(p.get('defenseSackA')),
            'def_sack_yds': abs(_safe_int(p.get('defenseSackWinLossYards'))),
            'def_brup':    _safe_int(p.get('defensePassBrUp')),
            'def_qbh':     _safe_int(p.get('defenseQbh')),
            'def_ff':      _safe_int(p.get('defenseFumbForc')),
            'def_fr':      _safe_int(p.get('defenseFumbRcvr')),
            'def_blk':     _safe_int(p.get('defenseBlkdKick')),
            'def_saf':     _safe_int(p.get('defenseSaf')),
            'def_int_no':  _safe_int(p.get('returnsIntNo')),
            'def_int_yds': _safe_int(p.get('returnsIntYards')),
            'penalty_no':  _safe_int(p.get('penaltyNo')),
            'penalty_yds': _safe_int(p.get('penaltyYards')),
        }

    def _team_view(t):
        players = [_player_view(p) for p in (t.get('players') or []) if _participated(p)]
        groups = {
            'passing':   [p for p in players if p['pass_att']],
            'rushing':   [p for p in players if p['rush_att']],
            'receiving': [p for p in players if p['rec_no']],
            'kicking':   [p for p in players if (p['fg_att'] or p['pat_kick_at'])],
            'punting':   [p for p in players if p['punt_no']],
            'kickoffs':  [p for p in players if p['ko_no']],
            'kr':        [p for p in players if p['kr_no']],
            'pr':        [p for p in players if p['pr_no']],
            'ir':        [p for p in players if p['ir_no']],
            'fr':        [p for p in players if p['fr_no']],
            'fumbles':   [p for p in players if p['fumb_no']],
            'defense':   [p for p in players if (
                p['def_tack_ua'] + p['def_tack_a'] + p['def_sack_ua'] + p['def_sack_a']
                + p['def_brup'] + p['def_qbh'] + p['def_ff'] + p['def_fr']
                + p['def_int_no'] + p['def_blk'] + p['def_saf'] + p['def_tfl_ua'] + p['def_tfl_a']
            )],
        }
        return {
            'name':  t.get('customizedName') or t.get('name') or '',
            'short': t.get('name') or '',
            'abbr':  t.get('abbr') or '',
            'record': t.get('record') or t.get('record_gen') or '0-0',
            'conf_record': t.get('record_conf') or '0',
            'totals': _team_totals(t),
            'players': players,
            'groups': groups,
        }

    vis = _team_view(vis_team)
    home = _team_view(home_team)

    # Scoring summary (rows)
    scoring = []
    for sc in (ev.get('scoring') or []):
        passer = sc.get('passer')
        scorer = sc.get('scorer')
        pat_by = sc.get('patBy')
        team_view = home if sc.get('homeTeam') else vis
        scoring.append({
            'qtr':   _safe_int(sc.get('quarter'), 0),
            'clock': _mmss(_safe_int(sc.get('mins'), 0) * 60 + _safe_int(sc.get('secs'), 0)),
            'how':   sc.get('how') or '',
            'type':  sc.get('type') or '',
            'yards': _safe_int(sc.get('yards'), 0),
            'v_score': _safe_int(sc.get('visitorScore'), 0),
            'h_score': _safe_int(sc.get('homeScore'), 0),
            'home_team': bool(sc.get('homeTeam')),
            'team_abbr': team_view['abbr'],
            'team_name': team_view['short'] or team_view['name'],
            'scorer': _uni_to_name(team_view, scorer),
            'passer': _uni_to_name(team_view, passer),
            'pat_by': _uni_to_name(team_view, pat_by),
            'pat_type': _PAT_CODE_MAP.get(_safe_int(sc.get('patCode'), -1), ('', ''))[0],
            'pat_res':  _PAT_CODE_MAP.get(_safe_int(sc.get('patCode'), -1), ('', ''))[1],
            'drive_idx': _safe_int(sc.get('driveIdx'), 0),
        })

    drives = [_drive_view(d, vis, home, i + 1) for i, d in enumerate(ev.get('drives') or [])]

    return {
        'sport':         'football',
        'sport_id':      0,
        'game_id':       game.id,
        'date':          ev.get('date') or _date_us(game.date),
        'date_iso':      game.date or '',
        'location':      ev.get('location') or game.location or '',
        'start_time':    ev.get('timeStart') or game.start_time or '',
        'attendance':    _safe_int(ev.get('attendance')),
        'scorekeeper':   (ev.get('referees') or [''] * 9)[7] if (ev.get('referees') and len(ev.get('referees')) > 7) else (game.scorer or ''),
        'referees':      ev.get('referees') or [],
        'is_complete':   bool(game.is_complete),
        'has_lineup':    bool(game.has_lineup),
        'status_label':  game.status_label,
        'nperiods':      nperiods,
        'period_labels': [_qtr_label(i + 1) for i in range(nperiods)],
        'linescore':     linescore,
        'visitor':       vis,
        'home':          home,
        'visitor_score': vis_score,
        'home_score':    home_score,
        'starting_visitor': starting_visitor,
        'scoring':       scoring,
        'drives':        drives,
        'rules': {
            'periods':     int(ev.get('gamePeriods') or 4),
            'minutes':     int(ev.get('minutesPrd') or 15),
            'downs':       int(ev.get('downs') or 4),
            'first_down_yards': int(ev.get('firstDownYards') or 10),
            'pat_spot':    int(ev.get('patTrySpot') or 3),
            'kickoff_spot': int(ev.get('kickoffSpot') or 35),
            'tb_spot':     int(ev.get('touchBackSpot') or 25),
            'saf_spot':    int(ev.get('safetySpot') or 20),
            'field':       int(ev.get('fieldLength') or 100),
        },
        # The full blob is included so JSON consumers / debugging can drop a level.
        '_raw': blob,
    }


def _uni_to_name(team_view, uniform):
    if uniform in (None, '', -1, '-1'):
        return ''
    u = str(uniform)
    for p in team_view.get('players', []):
        if str(p.get('uni')) == u:
            return p.get('name') or ''
    return ''


def _drive_view(d, vis, home, idx):
    is_visitor = (d.get('awayTeam') is True) or (not d.get('awayTeam') and not d.get('homeTeam') and idx == 1) or (d.get('vh', '') == 'V')
    # The blob's `awayTeam` field tracks "team away from goal" — not "visiting team".
    # Use startSpot/endSpot ownership cues instead when in doubt; for our purposes,
    # we just record which team possessed the drive based on the score row's owner.
    return {
        'idx':       idx,
        'plays':     _safe_int(d.get('playsSum'), 0),
        'plays_a':   _safe_int(d.get('playsA'), 0),
        'plays_b':   _safe_int(d.get('playsB'), 0),
        'start_qtr': _safe_int(d.get('startQuarter'), 0),
        'end_qtr':   _safe_int(d.get('endQuarter'), 0),
        'start_secs':_safe_int(d.get('startSecs'), 0),
        'end_secs':  _safe_int(d.get('endSecs'), 0),
        'start_spot':_safe_int(d.get('startSpot'), 0),
        'end_spot':  _safe_int(d.get('endSpot'), 0),
        'start_how': d.get('startHow') or '',
        'end_how':   d.get('endHow') or '',
        'yards':     _safe_int(d.get('yards'), 0),
        'top':       _mmss(_safe_int(d.get('topSecs'), 0)),
        'top_secs':  _safe_int(d.get('topSecs'), 0),
        'red_zone':  bool(d.get('redZone')),
    }


# ── XML build ────────────────────────────────────────────────────────────

def _set(elem, **kw):
    """Set attributes in insertion order — ET preserves dict insertion since 3.8."""
    for k, v in kw.items():
        if v is None:
            v = ''
        elem.set(k, str(v))


def build_xml(game):
    """Build a Presto-format `<fbgame>` XML document from gwt_bs_blob."""
    blob = _load_blob(game)
    ev = blob.get('eventInfo') or {}
    teams = blob.get('teams') or [{}, {}]
    if len(teams) < 2:
        teams = teams + [{}] * (2 - len(teams))
    vis_t, home_t = teams[0] or {}, teams[1] or {}

    # Build helper: compute team-level XML view
    data = boxscore_data(game)
    vis_view, home_view = data['visitor'], data['home']

    root = ET.Element('fbgame')
    _set(root, source='PrestoSports', version='7.16.0',
         generated=_date.today().strftime('%m/%d/%Y'))

    # ── <venue>/<officials>/<rules> ────────────────────────────────────
    venue = ET.SubElement(root, 'venue')
    _set(venue,
         gameid='', visid=vis_view['abbr'], visname=vis_view['short'] or vis_view['name'],
         homeid=home_view['abbr'], homename=home_view['short'] or home_view['name'],
         date=ev.get('date') or _date_us(game.date),
         location=ev.get('location') or game.location or '',
         stadium=ev.get('arenaData', {}).get('stadium', '') if isinstance(ev.get('arenaData'), dict) else '',
         start=ev.get('timeStart') or game.start_time or '',
         end=ev.get('timeEnd') or '', duration=ev.get('duration') or '',
         delay=ev.get('delayDuration') or '',
         attend=str(_safe_int(ev.get('attendance'))),
         schednote='',
         leaguegame=('Y' if not ev.get('exhibition') else 'N'),
         neutralgame=('Y' if ev.get('neutral') else 'N'),
         postseason=('Y' if ev.get('postseason') else 'N'))

    refs = ev.get('referees') or []
    officials = ET.SubElement(venue, 'officials')
    for slot, key in zip(range(9), ('ref', 'ump', 'line', 'lj', 'bj', 'fj', 'sj', 'sc', 'cj')):
        officials.set(key, refs[slot] if slot < len(refs) else '')

    rules = ET.SubElement(venue, 'rules')
    _set(rules,
         qtrs=str(int(ev.get('gamePeriods') or 4)),
         mins=str(int(ev.get('minutesPrd') or 15)),
         downs=str(int(ev.get('downs') or 4)),
         yds=str(int(ev.get('firstDownYards') or 10)),
         kospot=str(int(ev.get('kickoffSpot') or 35)),
         kotbspot=str(int(ev.get('koFairCatchSpot') or 25)),
         tbspot=str(int(ev.get('touchBackSpot') or 25)),
         patspot=str(int(ev.get('patTrySpot') or 3)),
         safspot=str(int(ev.get('safetySpot') or 20)),
         td=str(int(ev.get('touchDown') or 6)),
         fg=str(int(ev.get('fieldGoal') or 3)),
         pat=str(int(ev.get('kickPat') or 1)),
         patx=str(int(ev.get('otherPat') or 2)),
         saf=str(int(ev.get('safety') or 2)),
         defpat=str(int(ev.get('defPat') or 2)),
         rouge=str(int(ev.get('rouge') or 1)),
         field=str(int(ev.get('fieldLength') or 100)),
         toh=str(int(ev.get('toHalf') or 3)),
         sackrush=('Y' if ev.get('sackRush') else 'N'),
         fgaplay=('Y' if ev.get('fgaDrvPlay') else 'N'),
         netpunttb=('Y' if ev.get('otherTouchBack') else 'N'))

    # ── <status> ───────────────────────────────────────────────────────
    status = ET.SubElement(root, 'status')
    _set(status,
         complete='Y' if game.is_complete else 'N',
         running=('F' if game.is_complete else 'T'),
         period=str(int(ev.get('statusPeriod') or data['nperiods'] or 4)),
         clock=_mmss(_safe_int(ev.get('statusMinutes')) * 60 + _safe_int(ev.get('statusSeconds'))))

    # ── two <team> blocks (V, H) ───────────────────────────────────────
    def _emit_team(team_view, raw_team, vh):
        team = ET.SubElement(root, 'team')
        _set(team,
             vh=vh, code=team_view['short'] or team_view['name'], id=team_view['abbr'],
             name=team_view['short'] or team_view['name'],
             record=str(raw_team.get('record_gen') or raw_team.get('record') or '0-0'),
             **{'conf-record': str(raw_team.get('record_conf') or '0')},
             abb=(raw_team.get('keyStroke') or (team_view['short'] or 'X')[:1]))

        periods = raw_team.get('periodstats') or []
        score_total = sum(_safe_int(p.get('score')) for p in periods if _safe_int(p.get('score')) != 99)
        line_vals = [
            ('X' if _safe_int(p.get('score')) == 99 else str(_safe_int(p.get('score'))))
            for p in periods
        ]
        linescore = ET.SubElement(team, 'linescore')
        _set(linescore, prds=str(len(periods)), line=','.join(line_vals), score=str(score_total))
        for i, p in enumerate(periods):
            sub = ET.SubElement(linescore, 'lineprd')
            sc = _safe_int(p.get('score'))
            _set(sub, prd=str(i + 1), score=('X' if sc == 99 else str(sc)))

        T = team_view['totals']
        totals = ET.SubElement(team, 'totals')
        _set(totals,
             totoff_plays=str(T['plays']),
             totoff_yards=str(T['total_yds']),
             totoff_avg=_div(T['total_yds'], T['plays'], 1))

        _set(ET.SubElement(totals, 'firstdowns'),
             no=str(T['first_downs']), rush=str(T['fd_rush']),
             **{'pass': str(T['fd_pass'])}, penalty=str(T['fd_penalty']))
        _set(ET.SubElement(totals, 'penalties'), no=str(T['pen_no']), yds=str(T['pen_yds']))
        _set(ET.SubElement(totals, 'conversions'),
             thirdconv=str(T['third_conv']), thirdatt=str(T['third_att']),
             fourthconv=str(T['fourth_conv']), fourthatt=str(T['fourth_att']))
        _set(ET.SubElement(totals, 'fumbles'), no=str(T['fumb_no']), lost=str(T['fumb_lost']))
        _set(ET.SubElement(totals, 'misc'), top=T['top'], ona='0', onm='0', yds='0')
        _set(ET.SubElement(totals, 'redzone'),
             att=str(T['rz_att']), scores=str(T['rz_scores']), points=str(T['rz_points']),
             tdrush=str(T['rz_td_rush']), tdpass=str(T['rz_td_pass']),
             fgmade=str(T['rz_fg']), endfga=str(T['rz_end_fga']),
             enddowns=str(T['rz_end_dn']), endint=str(T['rz_end_int']),
             endfumb=str(T['rz_end_fumb']), endhalf=str(T['rz_end_half']),
             endgame=str(T['rz_end_game']))
        _set(ET.SubElement(totals, 'rush'),
             att=str(T['rush_att']), td='0', long=str(T['rush_yds'] if False else _safe_int(raw_team.get('rushLong'))),
             loss=str(T['rush_loss']), yds=str(T['rush_yds']), gain=str(T['rush_gain']))
        _set(ET.SubElement(totals, 'pass'),
             att=str(T['pass_att']), td=str(T['pass_td']),
             long=str(_safe_int(raw_team.get('passLong'))),
             yds=str(T['pass_yds']), comp=str(T['pass_comp']),
             sacks=str(T['pass_sacks']), sackyds=str(T['pass_sack_yds']),
             **{'int': str(T['pass_int'])})
        _set(ET.SubElement(totals, 'rcv'),
             long=str(_safe_int(raw_team.get('receivingLong'))),
             no=str(_safe_int(raw_team.get('receivingNo'))),
             td=str(_safe_int(raw_team.get('receivingTd'))),
             yds=str(_safe_int(raw_team.get('receivingYards'))))
        _set(ET.SubElement(totals, 'punt'),
             avg=_div(T['punt_yds'], T['punt_no'], 1), blkd='0',
             fc=str(_safe_int(raw_team.get('kickPuntFc'))),
             inside20=str(T['punt_i20']), long=str(T['punt_long']),
             no=str(T['punt_no']), plus50=str(T['punt_50']),
             tb=str(T['punt_tb']), yds=str(T['punt_yds']))
        _set(ET.SubElement(totals, 'ko'),
             no=str(T['ko_no']), ob=str(T['ko_ob']),
             tb=str(T['ko_tb']), yds=str(T['ko_yds']))
        if T['fg_att']:
            _set(ET.SubElement(totals, 'fg'),
                 att=str(T['fg_att']), blkd=str(T['fg_blk']),
                 long=str(T['fg_long']), made=str(T['fg_made']))
        _set(ET.SubElement(totals, 'pat'),
             kickatt=str(T['pat_kick_at']), kickmade=str(T['pat_kick_md']),
             passatt=str(T['pat_pass_at']), passmade=str(T['pat_pass_md']),
             rcvmade=str(T['pat_rcv_md']),
             rushmade=str(T['pat_rush_md']))
        _set(ET.SubElement(totals, 'defense'),
             brup=str(T['def_brup']), ff=str(T['def_fumb_forc']),
             fr=str(T['def_fumb_rcvr']),
             fryds=str(_safe_int(raw_team.get('returnsFumbYards'))),
             sacks=str(T['def_sack_ua'] + T['def_sack_a']),
             sackyds=str(T['def_sack_yds']),
             sacksa=str(T['def_sack_a']), sacksua=str(T['def_sack_ua']),
             tacka=str(T['def_tack_a']), tackua=str(T['def_tack_ua']),
             tot_tack=str(T['def_tack_a'] + T['def_tack_ua']),
             tflua=str(T['def_tfl_ua']), tflyds=str(T['def_tfl_yds']))
        if T['kr_no']:
            _set(ET.SubElement(totals, 'kr'),
                 long=str(T['kr_long']), no=str(T['kr_no']),
                 td=str(T['kr_td']), yds=str(T['kr_yds']))
        if T['pr_no']:
            _set(ET.SubElement(totals, 'pr'),
                 long=str(T['pr_long']), no=str(T['pr_no']),
                 td=str(T['pr_td']), yds=str(T['pr_yds']))
        if T['fr_no']:
            _set(ET.SubElement(totals, 'fr'),
                 long=str(T['fr_long']), no=str(T['fr_no']),
                 td=str(T['fr_td']), yds=str(T['fr_yds']))
        if T['ir_no']:
            _set(ET.SubElement(totals, 'ir'),
                 long=str(T['ir_long']), no=str(T['ir_no']),
                 td=str(T['ir_td']), yds=str(T['ir_yds']))
        # <scoring> summary at team level (counts only)
        sc = ET.SubElement(totals, 'scoring')
        _set(sc,
             td=str(_safe_int(raw_team.get('rushTd')) + _safe_int(raw_team.get('passTd'))
                    + _safe_int(raw_team.get('returnsKickTd')) + _safe_int(raw_team.get('returnsPuntTd'))
                    + _safe_int(raw_team.get('returnsFumbTd')) + _safe_int(raw_team.get('returnsIntTd'))),
             patkick=str(T['pat_kick_md']),
             patrcv=str(T['pat_rcv_md']))
        if T['fg_made']:
            sc.set('fg', str(T['fg_made']))
        if T['pat_pass_md']:
            sc.set('patpass', str(T['pat_pass_md']))
        if T['pat_rush_md']:
            sc.set('patrush', str(T['pat_rush_md']))

        # ── per-player rows ─────────────────────────────────────────
        for raw_p, view_p in zip(raw_team.get('players') or [], (raw_team.get('players') or [])):
            if not view_p:
                continue
            # Note: player loop uses raw players (we have the same source for view+raw).
            pass
        for p in (raw_team.get('players') or []):
            # We only emit participants — same logic as boxscore_data
            participated = bool(p.get('participated')) or any(
                _safe_int(p.get(k)) for k in
                ('rushAtt', 'passAtt', 'receivingNo', 'kickKoNo', 'kickPuntNo',
                 'returnsKickNo', 'returnsPuntNo', 'returnsIntNo', 'returnsFumbNo',
                 'defenseTackUa', 'defenseTackA', 'defenseTflUa', 'defenseTflA',
                 'defensePassBrUp', 'defenseSackUa', 'defenseSackA', 'defenseQbh',
                 'defenseFumbForc', 'defenseFumbRcvr', 'defenseSaf', 'defenseBlkdKick',
                 'kickFgAtt', 'epOffKickAt', 'epOffPassAt', 'epOffRushAt'))
            pl = ET.SubElement(team, 'player')
            uni = p.get('uniform') if p.get('uniform') is not None else ''
            name = p.get('completeName') or ''
            cn = _checkname(name)
            _set(pl,
                 uni=str(uni), name=name[:15], checkname=cn,
                 shortname=(name[:15] or name),
                 gp=('1' if participated else '0'),
                 code=str(p.get('readOrder') if p.get('readOrder') is not None else uni),
                 playerId=str(p.get('playerId') or ''))
            _emit_player_stats(pl, p)
        return team

    _emit_team(vis_view, vis_t, 'V')
    _emit_team(home_view, home_t, 'H')

    # ── <scores> ───────────────────────────────────────────────────────
    scores = ET.SubElement(root, 'scores')
    drives_list = ev.get('drives') or []
    for sc in (ev.get('scoring') or []):
        s = ET.SubElement(scores, 'score')
        team_view = home_view if sc.get('homeTeam') else vis_view
        oppo_view = vis_view if sc.get('homeTeam') else home_view
        pat_type, pat_res = _PAT_CODE_MAP.get(_safe_int(sc.get('patCode'), -1), ('', ''))
        scorer = _uni_to_name(team_view, sc.get('scorer'))
        passer = _uni_to_name(team_view, sc.get('passer'))
        pat_by = _uni_to_name(team_view, sc.get('patBy'))
        attrs = {
            'how':   sc.get('how') or '',
            'patby': pat_by,
            'qtr':   str(_safe_int(sc.get('quarter'))),
            'team':  team_view['abbr'],
            'scorer': scorer,
            'vh':    'H' if sc.get('homeTeam') else 'V',
            'type':  sc.get('type') or '',
            'clock': _mmss(_safe_int(sc.get('mins')) * 60 + _safe_int(sc.get('secs'))),
            'driveindex': str(_safe_int(sc.get('driveIdx'))),
            'hscore': str(_safe_int(sc.get('homeScore'))),
            'vscore': str(_safe_int(sc.get('visitorScore'))),
            'yds':   str(_safe_int(sc.get('yards'))),
        }
        if passer:
            attrs['passer'] = passer
        # Pull drive top/plays
        idx = _safe_int(sc.get('driveIdx'))
        if 0 < idx <= len(drives_list):
            d = drives_list[idx - 1]
            attrs['top']   = _mmss(_safe_int(d.get('topSecs')))
            attrs['plays'] = str(_safe_int(d.get('playsSum')))
            attrs['drive'] = str(_safe_int(d.get('yards')))
        if pat_res and (sc.get('type') or '').upper() == 'TD':
            attrs['patres']  = pat_res
            attrs['pattype'] = pat_type
        for k, v in attrs.items():
            s.set(k, v)

    # ── <fgas> ─────────────────────────────────────────────────────────
    fgas = ET.SubElement(root, 'fgas')
    for sc in (ev.get('scoring') or []):
        if (sc.get('type') or '').upper() != 'FG':
            continue
        team_view = home_view if sc.get('homeTeam') else vis_view
        f = ET.SubElement(fgas, 'fga')
        _set(f,
             distance=str(_safe_int(sc.get('yards'))),
             clock=_mmss(_safe_int(sc.get('mins')) * 60 + _safe_int(sc.get('secs'))),
             qtr=str(_safe_int(sc.get('quarter'))),
             result='good',
             team=team_view['abbr'],
             vh=('H' if sc.get('homeTeam') else 'V'),
             kicker=_uni_to_name(team_view, sc.get('scorer')))

    # ── <drives> ───────────────────────────────────────────────────────
    drives = ET.SubElement(root, 'drives')
    for i, d in enumerate(drives_list):
        dv = ET.SubElement(drives, 'drive')
        _set(dv,
             driveindex=str(i + 1),
             vh='', yards=str(_safe_int(d.get('yards'))),
             top=_mmss(_safe_int(d.get('topSecs'))),
             end=(f"{d.get('endHow') or ''},{_safe_int(d.get('endQuarter'))},"
                  f"{_mmss(_safe_int(d.get('endSecs')))},{_safe_int(d.get('endSpot'))}"),
             end_how=d.get('endHow') or '',
             end_qtr=str(_safe_int(d.get('endQuarter'))),
             end_time=_mmss(_safe_int(d.get('endSecs'))),
             end_spot=str(_safe_int(d.get('endSpot'))),
             start=(f"{d.get('startHow') or ''},{_safe_int(d.get('startQuarter'))},"
                    f"{_mmss(_safe_int(d.get('startSecs')))},{_safe_int(d.get('startSpot'))}"),
             start_how=d.get('startHow') or '',
             start_qtr=str(_safe_int(d.get('startQuarter'))),
             start_time=_mmss(_safe_int(d.get('startSecs'))),
             start_spot=str(_safe_int(d.get('startSpot'))),
             plays=str(_safe_int(d.get('playsSum'))),
             team='')
        if d.get('redZone'):
            dv.set('rz', '1')

    # ── <plays> ────────────────────────────────────────────────────────
    plays_el = ET.SubElement(root, 'plays')
    plays_el.set('format', 'summary')

    # ── <message> ──────────────────────────────────────────────────────
    msg = ET.SubElement(root, 'message')
    msg.set('text', (ev.get('notes') or ''))

    # ── DNP roster (players with gp=0) ─────────────────────────────────
    for raw_team, view, vh in ((vis_t, vis_view, 'V'), (home_t, home_view, 'H')):
        dnp_players = [
            p for p in (raw_team.get('players') or [])
            if not bool(p.get('participated')) and not any(
                _safe_int(p.get(k)) for k in
                ('rushAtt', 'passAtt', 'receivingNo', 'kickKoNo', 'kickPuntNo',
                 'returnsKickNo', 'returnsPuntNo', 'returnsIntNo', 'returnsFumbNo',
                 'defenseTackUa', 'defenseTackA', 'defenseTflUa', 'defenseTflA',
                 'defensePassBrUp', 'defenseSackUa', 'defenseSackA', 'defenseQbh',
                 'defenseFumbForc', 'defenseFumbRcvr', 'defenseSaf', 'defenseBlkdKick',
                 'kickFgAtt', 'epOffKickAt', 'epOffPassAt', 'epOffRushAt'))
        ]
        if not dnp_players:
            continue
        dnp_el = ET.SubElement(root, 'dnp')
        _set(dnp_el, id=view['abbr'], vh=vh)
        for p in dnp_players:
            name = p.get('completeName') or ''
            pl = ET.SubElement(dnp_el, 'player')
            _set(pl,
                 checkname=_checkname(name),
                 code=str(p.get('readOrder') if p.get('readOrder') is not None else _safe_int(p.get('uniform'))),
                 name=name[:15], uni=str(p.get('uniform') or ''), gp='0')

    _indent(root)
    xml_bytes = ET.tostring(root, encoding='unicode', xml_declaration=False)
    xml_bytes = re.sub(r'<([a-zA-Z0-9_]+)([^>]*?)\s*/>', r'<\1\2></\1>', xml_bytes)
    return '<?xml version="1.0" encoding="UTF-8"?>\n\n' + xml_bytes


def _emit_player_stats(pl, p):
    """Emit a player's per-category stat children inside <player>."""
    pa  = _safe_int(p.get('passAtt'))
    rcv = _safe_int(p.get('receivingNo'))
    ra  = _safe_int(p.get('rushAtt'))
    if ra:
        rl = _safe_int(p.get('rushWinLoss'))
        ry = _safe_int(p.get('rushYards'))
        _set(ET.SubElement(pl, 'rush'),
             att=str(ra), td=str(_safe_int(p.get('rushTd'))),
             long=str(_safe_int(p.get('rushLong'))), loss=str(abs(rl)),
             yds=str(ry), gain=str(ry - rl))
    if pa:
        _set(ET.SubElement(pl, 'pass'),
             att=str(pa), td=str(_safe_int(p.get('passTd'))),
             long=str(_safe_int(p.get('passLong'))),
             yds=str(_safe_int(p.get('passYards'))),
             comp=str(_safe_int(p.get('passComp'))),
             sacks=str(_safe_int(p.get('passSacks'))),
             sackyds=str(_safe_int(p.get('passSackYards'))),
             **{'int': str(_safe_int(p.get('passInt')))})
    if rcv:
        _set(ET.SubElement(pl, 'rcv'),
             long=str(_safe_int(p.get('receivingLong'))),
             no=str(rcv), td=str(_safe_int(p.get('receivingTd'))),
             yds=str(_safe_int(p.get('receivingYards'))))
    if _safe_int(p.get('kickPuntNo')):
        _set(ET.SubElement(pl, 'punt'),
             avg=f"{_punt_avg(p.get('kickPuntAvg')):.1f}",
             blkd=str(_safe_int(p.get('kickPuntBlkd'))),
             fc=str(_safe_int(p.get('kickPuntFc'))),
             inside20=str(_safe_int(p.get('kickPuntI20'))),
             long=str(_safe_int(p.get('kickPuntLong'))),
             no=str(_safe_int(p.get('kickPuntNo'))),
             plus50=str(_safe_int(p.get('kickPunt50'))),
             tb=str(_safe_int(p.get('kickPuntTb'))),
             yds=str(_safe_int(p.get('kickPuntYards'))))
    if _safe_int(p.get('kickKoNo')):
        _set(ET.SubElement(pl, 'ko'),
             no=str(_safe_int(p.get('kickKoNo'))),
             ob=str(_safe_int(p.get('kickKoOb'))),
             tb=str(_safe_int(p.get('kickKoTb'))),
             yds=str(_safe_int(p.get('kickKoYards'))))
    if _safe_int(p.get('kickFgAtt')) or _safe_int(p.get('kickFgMad')):
        _set(ET.SubElement(pl, 'fg'),
             att=str(_safe_int(p.get('kickFgAtt'))),
             blkd=str(_safe_int(p.get('kickFgBlk'))),
             long=str(_safe_int(p.get('kickFgLong'))),
             made=str(_safe_int(p.get('kickFgMad'))))
    if any(_safe_int(p.get(k)) for k in
           ('epOffKickAt', 'epOffPassAt', 'epOffRushAt', 'epOffRcvMd',
            'epOffKickMd', 'epOffPassMd', 'epOffRushMd')):
        _set(ET.SubElement(pl, 'pat'),
             kickatt=str(_safe_int(p.get('epOffKickAt'))),
             kickmade=str(_safe_int(p.get('epOffKickMd'))),
             passatt=str(_safe_int(p.get('epOffPassAt'))),
             passmade=str(_safe_int(p.get('epOffPassMd'))),
             rcvmade=str(_safe_int(p.get('epOffRcvMd'))),
             retfatt='0', retfmade='0', retkatt='0', retkmade='0',
             rushatt=str(_safe_int(p.get('epOffRushAt'))),
             rushmade=str(_safe_int(p.get('epOffRushMd'))))
    if any(_safe_int(p.get(k)) for k in
           ('defenseTackUa', 'defenseTackA', 'defenseTflUa', 'defenseTflA',
            'defensePassBrUp', 'defenseSackUa', 'defenseSackA', 'defenseQbh',
            'defenseFumbForc', 'defenseFumbRcvr', 'defenseSaf', 'defenseBlkdKick')):
        attrs = {}
        for x_attr, src_key in (
            ('brup', 'defensePassBrUp'),
            ('ff',   'defenseFumbForc'),
            ('fr',   'defenseFumbRcvr'),
            ('qbh',  'defenseQbh'),
            ('sacka',  'defenseSackA'),
            ('sackua', 'defenseSackUa'),
            ('tacka',  'defenseTackA'),
            ('tackua', 'defenseTackUa'),
            ('tfla',   'defenseTflA'),
            ('tflua',  'defenseTflUa'),
        ):
            v = _safe_int(p.get(src_key))
            if v:
                attrs[x_attr] = str(v)
        if _safe_int(p.get('defenseTackA')) + _safe_int(p.get('defenseTackUa')):
            attrs['tot_tack'] = str(_safe_int(p.get('defenseTackA')) + _safe_int(p.get('defenseTackUa')))
        if _safe_int(p.get('defenseSackWinLossYards')):
            attrs['sackyds'] = str(abs(_safe_int(p.get('defenseSackWinLossYards'))))
        if _safe_int(p.get('defenseTflLossYards')):
            attrs['tflyds']  = str(abs(_safe_int(p.get('defenseTflLossYards'))))
        if _safe_int(p.get('returnsFumbYards')):
            attrs['fryds']   = str(_safe_int(p.get('returnsFumbYards')))
        d = ET.SubElement(pl, 'defense')
        for k, v in attrs.items():
            d.set(k, v)
    if _safe_int(p.get('returnsKickNo')):
        _set(ET.SubElement(pl, 'kr'),
             long=str(_safe_int(p.get('returnsKickLong'))),
             no=str(_safe_int(p.get('returnsKickNo'))),
             td=str(_safe_int(p.get('returnsKickTd'))),
             yds=str(_safe_int(p.get('returnsKickYards'))))
    if _safe_int(p.get('returnsPuntNo')):
        _set(ET.SubElement(pl, 'pr'),
             long=str(_safe_int(p.get('returnsPuntLong'))),
             no=str(_safe_int(p.get('returnsPuntNo'))),
             td=str(_safe_int(p.get('returnsPuntTd'))),
             yds=str(_safe_int(p.get('returnsPuntYards'))))
    if _safe_int(p.get('returnsIntNo')):
        _set(ET.SubElement(pl, 'ir'),
             long=str(_safe_int(p.get('returnsIntLong'))),
             no=str(_safe_int(p.get('returnsIntNo'))),
             td=str(_safe_int(p.get('returnsIntTd'))),
             yds=str(_safe_int(p.get('returnsIntYards'))))
    if _safe_int(p.get('returnsFumbNo')):
        _set(ET.SubElement(pl, 'fr'),
             long=str(_safe_int(p.get('returnsFumbLong'))),
             no=str(_safe_int(p.get('returnsFumbNo'))),
             td=str(_safe_int(p.get('returnsFumbTd'))),
             yds=str(_safe_int(p.get('returnsFumbYards'))))
    if _safe_int(p.get('fumblesNo')):
        _set(ET.SubElement(pl, 'fumbles'),
             no=str(_safe_int(p.get('fumblesNo'))),
             lost=str(_safe_int(p.get('fumblesLost'))))
    # Scoring (per-player TDs / PATs)
    sc_attrs = {}
    td = sum(_safe_int(p.get(k)) for k in
             ('rushTd', 'passTd', 'receivingTd',
              'returnsKickTd', 'returnsPuntTd', 'returnsFumbTd', 'returnsIntTd'))
    if td:
        sc_attrs['td'] = str(td)
    if _safe_int(p.get('epOffKickMd')):
        sc_attrs['patkick'] = str(_safe_int(p.get('epOffKickMd')))
    if _safe_int(p.get('epOffPassMd')):
        sc_attrs['patpass'] = str(_safe_int(p.get('epOffPassMd')))
    if _safe_int(p.get('epOffRushMd')):
        sc_attrs['patrush'] = str(_safe_int(p.get('epOffRushMd')))
    if _safe_int(p.get('epOffRcvMd')):
        sc_attrs['patrcv']  = str(_safe_int(p.get('epOffRcvMd')))
    if _safe_int(p.get('kickFgMad')):
        sc_attrs['fg']      = str(_safe_int(p.get('kickFgMad')))
    if sc_attrs:
        sc = ET.SubElement(pl, 'scoring')
        for k, v in sc_attrs.items():
            sc.set(k, v)


def _checkname(name):
    """Convert 'First Last' → 'LAST,FIRST' truncated to Presto's 15-char field."""
    if not name:
        return ''
    parts = [s for s in name.strip().split() if s]
    if len(parts) == 1:
        return parts[0].upper()[:15]
    last = parts[-1].upper()
    first = ' '.join(parts[:-1]).upper()
    return f'{last},{first}'[:15]


def _indent(elem, level=0):
    """Two-space indent for readability (matches Presto export)."""
    i = '\n' + level * '  '
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + '  '
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


# ── Sport façade ─────────────────────────────────────────────────────────

class FootballSport(Sport):
    sport_id = 0
    name = 'Football'

    def status_options(self):
        # Mirrors app.routes._football_status_options() so the registry stays
        # the single source of truth as more sports are added.
        return [
            {'value': '1', 'label': '1st Quarter'},
            {'value': '2', 'label': '2nd Quarter'},
            {'value': '3', 'label': 'Halftime'},
            {'value': '4', 'label': '3rd Quarter'},
            {'value': '5', 'label': '4th Quarter'},
            {'value': '6', 'label': 'End of Regulation'},
            {'value': '7', 'label': 'Overtime'},
            {'value': '8', 'label': 'Final'},
            {'value': '9', 'label': 'Final - OT'},
        ]

    def boxscore_data(self, game):
        return boxscore_data(game)

    def build_xml(self, game):
        return build_xml(game)

    def render_html(self, game, **ctx):
        data = self.boxscore_data(game)
        return render_template('football_boxscore.html', game=game, data=data, **ctx)

    def render_pdf(self, game, style='full', **ctx):
        data = self.boxscore_data(game)
        style = (style or 'full').lower()
        if style == 'summary':
            return render_template('football_pdf_summary.html', game=game, data=data, **ctx)
        if style == 'pbp':
            try:
                qtr = int(request.args.get('qtr', '1'))
            except Exception:
                qtr = 1
            return render_template('football_pdf_pbp.html', game=game, data=data, qtr=qtr, **ctx)
        return render_template('football_pdf_full.html', game=game, data=data, **ctx)

    def persist_save(self, game, bs, statuscode=-2, live_stats_raw=''):
        # Football lives entirely in gwt_bs_blob — store it, sync the line
        # score off `periodstats`, and leave the diamond-only tables alone.
        super().persist_save(game, bs, statuscode=statuscode, live_stats_raw=live_stats_raw)
