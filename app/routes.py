"""Flask routes for the baseball stats app."""

import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from sqlalchemy import func
from app import db
from app.models import (
    League, Team, Player, Game,
    BattingStats, PitchingStats, FieldingStats, Play, InningScore,
)
from app.xml_parser import parse_game_xml

main_bp = Blueprint("main", __name__)
api_bp = Blueprint("api", __name__)


# ── Helper functions ──────────────────────────────────────────────────────────


def _aggregate_batting(stats_query):
    """Aggregate batting stats from a list of BattingStats objects."""
    stats = stats_query
    if not stats:
        return None
    totals = {
        "gp": len(set(s.game_id for s in stats)),
        "ab": sum(s.ab for s in stats),
        "r": sum(s.r for s in stats),
        "h": sum(s.h for s in stats),
        "rbi": sum(s.rbi for s in stats),
        "doubles": sum(s.doubles for s in stats),
        "triples": sum(s.triples for s in stats),
        "hr": sum(s.hr for s in stats),
        "bb": sum(s.bb for s in stats),
        "so": sum(s.so for s in stats),
        "sb": sum(s.sb for s in stats),
        "cs": sum(s.cs for s in stats),
        "hbp": sum(s.hbp for s in stats),
        "sh": sum(s.sh for s in stats),
        "sf": sum(s.sf for s in stats),
        "gdp": sum(s.gdp for s in stats),
        "kl": sum(s.kl for s in stats),
    }
    ab = totals["ab"]
    h = totals["h"]
    bb = totals["bb"]
    hbp = totals["hbp"]
    sf = totals["sf"]
    singles = h - totals["doubles"] - totals["triples"] - totals["hr"]
    tb = singles + 2 * totals["doubles"] + 3 * totals["triples"] + 4 * totals["hr"]

    totals["avg"] = f"{h / ab:.3f}" if ab > 0 else ".000"
    denom = ab + bb + hbp + sf
    totals["obp"] = f"{(h + bb + hbp) / denom:.3f}" if denom > 0 else ".000"
    totals["slg"] = f"{tb / ab:.3f}" if ab > 0 else ".000"
    obp_val = (h + bb + hbp) / denom if denom > 0 else 0.0
    slg_val = tb / ab if ab > 0 else 0.0
    totals["ops"] = f"{obp_val + slg_val:.3f}"
    return totals


def _aggregate_pitching(stats_list):
    """Aggregate pitching stats from a list of PitchingStats objects."""
    if not stats_list:
        return None

    # IP is stored as e.g. 4.1 meaning 4 and 1/3
    total_thirds = 0
    for s in stats_list:
        ip_full = int(s.ip)
        ip_frac = round((s.ip - ip_full) * 10)
        total_thirds += ip_full * 3 + ip_frac

    ip_display_full = total_thirds // 3
    ip_display_frac = total_thirds % 3
    ip_display = f"{ip_display_full}.{ip_display_frac}" if ip_display_frac else str(ip_display_full)

    totals = {
        "gp": len(set(s.game_id for s in stats_list)),
        "gs": sum(s.gs for s in stats_list),
        "ip": ip_display,
        "h": sum(s.h for s in stats_list),
        "r": sum(s.r for s in stats_list),
        "er": sum(s.er for s in stats_list),
        "bb": sum(s.bb for s in stats_list),
        "so": sum(s.so for s in stats_list),
        "hr": sum(s.hr for s in stats_list),
        "hbp": sum(s.hbp for s in stats_list),
        "bf": sum(s.bf for s in stats_list),
        "wp": sum(s.wp for s in stats_list),
        "bk": sum(s.bk for s in stats_list),
        "pitches": sum(s.pitches for s in stats_list),
        "strikes": sum(s.strikes for s in stats_list),
        "cg": sum(s.cg for s in stats_list),
        "sho": sum(s.sho for s in stats_list),
        "w": sum(1 for s in stats_list if s.win),
        "l": sum(1 for s in stats_list if s.loss),
        "sv": sum(1 for s in stats_list if s.save),
    }

    er = totals["er"]
    # ERA based on scheduled innings (7 for softball default)
    totals["era"] = f"{(er * 7 * 3) / total_thirds:.2f}" if total_thirds > 0 else "0.00"
    totals["whip"] = f"{(totals['bb'] + totals['h']) / (total_thirds / 3):.2f}" if total_thirds > 0 else "0.00"
    return totals


def _aggregate_fielding(stats_list):
    """Aggregate fielding stats."""
    if not stats_list:
        return None
    totals = {
        "gp": len(set(s.game_id for s in stats_list)),
        "po": sum(s.po for s in stats_list),
        "a": sum(s.a for s in stats_list),
        "e": sum(s.e for s in stats_list),
        "pb": sum(s.pb for s in stats_list),
        "sba": sum(s.sba for s in stats_list),
    }
    tc = totals["po"] + totals["a"] + totals["e"]
    totals["tc"] = tc
    totals["fpct"] = f"{(totals['po'] + totals['a']) / tc:.3f}" if tc > 0 else "1.000"
    return totals


# ── Main routes ───────────────────────────────────────────────────────────────


@main_bp.route("/")
def index():
    leagues = League.query.all()
    if len(leagues) == 1:
        return redirect(url_for("main.league_detail", league_id=leagues[0].id))
    return render_template("index.html", leagues=leagues)


@main_bp.route("/league/new", methods=["GET", "POST"])
def league_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        sport = request.form.get("sport", "softball")
        season = request.form.get("season", "").strip()
        if not name:
            flash("League name is required.", "error")
            return render_template("league_new.html")
        league = League(name=name, sport=sport, season=season)
        db.session.add(league)
        db.session.commit()
        flash(f"League '{name}' created.", "success")
        return redirect(url_for("main.league_detail", league_id=league.id))
    return render_template("league_new.html")


@main_bp.route("/league/<int:league_id>")
def league_detail(league_id):
    league = League.query.get_or_404(league_id)
    teams = Team.query.filter_by(league_id=league.id).all()

    # Build standings
    standings = []
    for team in teams:
        wins = Game.query.filter(
            ((Game.home_team_id == team.id) & (Game.home_runs > Game.visitor_runs)) |
            ((Game.visitor_team_id == team.id) & (Game.visitor_runs > Game.home_runs))
        ).filter(Game.is_complete == True).count()  # noqa: E712

        losses = Game.query.filter(
            ((Game.home_team_id == team.id) & (Game.home_runs < Game.visitor_runs)) |
            ((Game.visitor_team_id == team.id) & (Game.visitor_runs < Game.home_runs))
        ).filter(Game.is_complete == True).count()  # noqa: E712

        pct = wins / (wins + losses) if (wins + losses) > 0 else 0.0

        # Runs scored / allowed
        home_games = Game.query.filter_by(home_team_id=team.id, is_complete=True).all()
        away_games = Game.query.filter_by(visitor_team_id=team.id, is_complete=True).all()
        rs = sum(g.home_runs for g in home_games) + sum(g.visitor_runs for g in away_games)
        ra = sum(g.visitor_runs for g in home_games) + sum(g.home_runs for g in away_games)

        standings.append({
            "team": team,
            "w": wins,
            "l": losses,
            "pct": f"{pct:.3f}",
            "rs": rs,
            "ra": ra,
        })

    standings.sort(key=lambda x: (-float(x["pct"]), x["team"].name))

    # Recent games
    games = Game.query.filter(
        (Game.home_team_id.in_([t.id for t in teams])) |
        (Game.visitor_team_id.in_([t.id for t in teams]))
    ).order_by(Game.date.desc()).limit(20).all()

    return render_template("league_detail.html", league=league, standings=standings, games=games)


@main_bp.route("/team/<int:team_id>")
def team_detail(team_id):
    team = Team.query.get_or_404(team_id)
    players = Player.query.filter_by(team_id=team.id).all()

    # Player season batting stats
    batting_leaders = []
    for player in players:
        stats = BattingStats.query.filter_by(player_id=player.id, team_id=team.id).all()
        agg = _aggregate_batting(stats)
        if agg and agg["ab"] > 0:
            batting_leaders.append({"player": player, "stats": agg})
    batting_leaders.sort(key=lambda x: float(x["stats"]["avg"]), reverse=True)

    # Player season pitching stats
    pitching_leaders = []
    for player in players:
        stats = PitchingStats.query.filter_by(player_id=player.id, team_id=team.id).all()
        agg = _aggregate_pitching(stats)
        if agg and agg["ip"] != "0":
            pitching_leaders.append({"player": player, "stats": agg})
    pitching_leaders.sort(key=lambda x: float(x["stats"]["era"]))

    # Team schedule/results
    games = Game.query.filter(
        (Game.home_team_id == team.id) | (Game.visitor_team_id == team.id)
    ).order_by(Game.date).all()

    schedule = []
    for g in games:
        is_home = g.home_team_id == team.id
        opponent = g.visitor_team if is_home else g.home_team
        team_score = g.home_runs if is_home else g.visitor_runs
        opp_score = g.visitor_runs if is_home else g.home_runs
        if g.is_complete:
            result = "W" if team_score > opp_score else "L" if team_score < opp_score else "T"
        else:
            result = "-"
        schedule.append({
            "game": g,
            "opponent": opponent,
            "home_away": "vs" if is_home else "@",
            "result": result,
            "score": f"{team_score}-{opp_score}" if g.is_complete else "",
        })

    return render_template("team_detail.html", team=team, batting=batting_leaders,
                           pitching=pitching_leaders, schedule=schedule)


@main_bp.route("/player/<int:player_id>")
def player_detail(player_id):
    player = Player.query.get_or_404(player_id)

    batting_games = BattingStats.query.filter_by(player_id=player.id).all()
    batting_agg = _aggregate_batting(batting_games)

    pitching_games = PitchingStats.query.filter_by(player_id=player.id).all()
    pitching_agg = _aggregate_pitching(pitching_games)

    fielding_games = FieldingStats.query.filter_by(player_id=player.id).all()
    fielding_agg = _aggregate_fielding(fielding_games)

    # Game log
    game_log = []
    game_ids = set(s.game_id for s in batting_games)
    for gid in sorted(game_ids):
        game = Game.query.get(gid)
        bat = next((s for s in batting_games if s.game_id == gid), None)
        pitch = next((s for s in pitching_games if s.game_id == gid), None)
        game_log.append({"game": game, "batting": bat, "pitching": pitch})

    return render_template("player_detail.html", player=player,
                           batting=batting_agg, pitching=pitching_agg,
                           fielding=fielding_agg, game_log=game_log)


@main_bp.route("/game/<int:game_id>")
def game_detail(game_id):
    game = Game.query.get_or_404(game_id)
    innings = InningScore.query.filter_by(game_id=game.id).order_by(InningScore.inning).all()

    # Visitor batting
    v_batting = (
        BattingStats.query
        .filter_by(game_id=game.id, team_id=game.visitor_team_id)
        .join(Player)
        .order_by(BattingStats.batting_order)
        .all()
    )
    # Home batting
    h_batting = (
        BattingStats.query
        .filter_by(game_id=game.id, team_id=game.home_team_id)
        .join(Player)
        .order_by(BattingStats.batting_order)
        .all()
    )

    # Pitching
    v_pitching = PitchingStats.query.filter_by(game_id=game.id, team_id=game.visitor_team_id).all()
    h_pitching = PitchingStats.query.filter_by(game_id=game.id, team_id=game.home_team_id).all()

    # Play-by-play
    plays = Play.query.filter_by(game_id=game.id).order_by(Play.sequence).all()

    return render_template("game_detail.html", game=game, innings=innings,
                           v_batting=v_batting, h_batting=h_batting,
                           v_pitching=v_pitching, h_pitching=h_pitching,
                           plays=plays)


@main_bp.route("/upload", methods=["GET", "POST"])
def upload():
    leagues = League.query.all()
    if request.method == "POST":
        league_id = request.form.get("league_id")
        if not league_id:
            flash("Please select a league.", "error")
            return render_template("upload.html", leagues=leagues)

        league = League.query.get(league_id)
        if not league:
            flash("League not found.", "error")
            return render_template("upload.html", leagues=leagues)

        files = request.files.getlist("xmlfiles")
        if not files or all(f.filename == "" for f in files):
            flash("Please select at least one XML file.", "error")
            return render_template("upload.html", leagues=leagues)

        imported = 0
        errors = []
        for f in files:
            if f.filename == "":
                continue
            try:
                content = f.read()
                game = parse_game_xml(content, league)
                if game:
                    imported += 1
            except Exception as e:
                errors.append(f"{f.filename}: {str(e)}")

        if imported:
            flash(f"Successfully imported {imported} game(s).", "success")
        if errors:
            for err in errors:
                flash(f"Error: {err}", "error")

        return redirect(url_for("main.league_detail", league_id=league.id))

    return render_template("upload.html", leagues=leagues)


# ── API routes ────────────────────────────────────────────────────────────────


@api_bp.route("/leagues")
def api_leagues():
    leagues = League.query.all()
    return jsonify([{"id": l.id, "name": l.name, "sport": l.sport, "season": l.season} for l in leagues])


@api_bp.route("/teams/<int:team_id>/batting")
def api_team_batting(team_id):
    team = Team.query.get_or_404(team_id)
    players = Player.query.filter_by(team_id=team.id).all()
    results = []
    for player in players:
        stats = BattingStats.query.filter_by(player_id=player.id, team_id=team.id).all()
        agg = _aggregate_batting(stats)
        if agg and agg["ab"] > 0:
            agg["player_id"] = player.id
            agg["name"] = player.name
            agg["number"] = player.uniform_number
            results.append(agg)
    return jsonify(results)


@api_bp.route("/teams/<int:team_id>/pitching")
def api_team_pitching(team_id):
    team = Team.query.get_or_404(team_id)
    players = Player.query.filter_by(team_id=team.id).all()
    results = []
    for player in players:
        stats = PitchingStats.query.filter_by(player_id=player.id, team_id=team.id).all()
        agg = _aggregate_pitching(stats)
        if agg and agg["ip"] != "0":
            agg["player_id"] = player.id
            agg["name"] = player.name
            agg["number"] = player.uniform_number
            results.append(agg)
    return jsonify(results)
