"""Flask routes for the baseball stats app."""

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from app.models import (
    Season, Team, Player, Game,
    BattingStats, PitchingStats, FieldingStats, Play, InningScore,
)

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
    seasons = Season.query.all()
    return render_template("index.html", seasons=seasons)


@main_bp.route("/season/<int:season_id>")
def season_detail(season_id):
    season = Season.query.get_or_404(season_id)
    teams = Team.query.filter_by(season_id=season.id).all()

    # Recent games
    games = Game.query.filter(
        (Game.home_team_id.in_([t.id for t in teams])) |
        (Game.visitor_team_id.in_([t.id for t in teams]))
    ).order_by(Game.date.desc()).limit(20).all()

    return render_template("season_detail.html", season=season, teams=teams, games=games)


# ── Configure (System Preferences) ───────────────────────────────────────────


@main_bp.route("/configure")
def configure():
    seasons = Season.query.all()
    return render_template("system_preferences.html", seasons=seasons)


@main_bp.route("/configure/season", methods=["POST"])
def configure_season_create():
    name = request.form.get("name", "").strip()
    play_entry_mode = request.form.get("play_entry_mode", "basic")
    rules = request.form.get("rules", "softball")
    gender = request.form.get("gender", "female")
    if not name:
        flash("Season name is required.", "error")
        return redirect(url_for("main.configure"))
    season = Season(name=name, play_entry_mode=play_entry_mode, rules=rules, gender=gender)
    db.session.add(season)
    db.session.commit()
    flash(f"Season '{name}' created.", "success")
    return redirect(url_for("main.configure"))


@main_bp.route("/configure/season/<int:season_id>/edit", methods=["POST"])
def configure_season_edit(season_id):
    season = Season.query.get_or_404(season_id)
    season.name = request.form.get("name", season.name).strip()
    season.play_entry_mode = request.form.get("play_entry_mode", season.play_entry_mode)
    season.rules = request.form.get("rules", season.rules)
    season.gender = request.form.get("gender", season.gender)
    db.session.commit()
    flash(f"Season '{season.name}' updated.", "success")
    return redirect(url_for("main.configure"))


@main_bp.route("/configure/season/<int:season_id>/delete", methods=["POST"])
def configure_season_delete(season_id):
    season = Season.query.get_or_404(season_id)
    # Delete all teams and their players in this season
    for team in season.teams:
        Player.query.filter_by(team_id=team.id).delete()
        db.session.delete(team)
    db.session.delete(season)
    db.session.commit()
    flash("Season deleted.", "success")
    return redirect(url_for("main.configure"))


@main_bp.route("/configure/season/<int:season_id>/team", methods=["POST"])
def configure_team_create(season_id):
    season = Season.query.get_or_404(season_id)
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip()
    if not name:
        flash("Team name is required.", "error")
        return redirect(url_for("main.index"))
    if not code:
        code = name[:4].upper()
    team = Team(
        name=name, code=code, season_id=season.id,
        stadium=request.form.get("stadium", "").strip(),
        city=request.form.get("city", "").strip(),
        state=request.form.get("state", "").strip(),
        mascot=request.form.get("mascot", "").strip(),
        print_name=request.form.get("print_name", "").strip(),
        abbreviation=request.form.get("abbreviation", "").strip(),
        league=request.form.get("league", "").strip(),
        division=request.form.get("division", "").strip(),
        coach=request.form.get("coach", "").strip(),
        conference=request.form.get("conference", "").strip(),
    )
    db.session.add(team)
    db.session.commit()
    flash(f"Team '{name}' added to {season.name}.", "success")
    return redirect(url_for("main.index"))


@main_bp.route("/configure/team/<int:team_id>/edit", methods=["POST"])
def configure_team_edit(team_id):
    team = Team.query.get_or_404(team_id)
    team.name = request.form.get("name", team.name).strip()
    team.code = request.form.get("code", team.code).strip()
    team.stadium = request.form.get("stadium", team.stadium or "").strip()
    team.city = request.form.get("city", team.city or "").strip()
    team.state = request.form.get("state", team.state or "").strip()
    team.mascot = request.form.get("mascot", team.mascot or "").strip()
    team.print_name = request.form.get("print_name", team.print_name or "").strip()
    team.abbreviation = request.form.get("abbreviation", team.abbreviation or "").strip()
    team.league = request.form.get("league", team.league or "").strip()
    team.division = request.form.get("division", team.division or "").strip()
    team.coach = request.form.get("coach", team.coach or "").strip()
    team.conference = request.form.get("conference", team.conference or "").strip()
    db.session.commit()
    flash(f"Team '{team.name}' updated.", "success")
    return redirect(url_for("main.index"))


@main_bp.route("/configure/team/<int:team_id>/delete", methods=["POST"])
def configure_team_delete(team_id):
    team = Team.query.get_or_404(team_id)
    Player.query.filter_by(team_id=team.id).delete()
    db.session.delete(team)
    db.session.commit()
    flash("Team deleted.", "success")
    return redirect(url_for("main.configure"))


# ── Team / Player / Game detail routes ────────────────────────────────────────


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


# ── API routes ────────────────────────────────────────────────────────────────


@api_bp.route("/seasons")
def api_seasons():
    seasons = Season.query.all()
    return jsonify([{"id": s.id, "name": s.name, "rules": s.rules, "gender": s.gender, "play_entry_mode": s.play_entry_mode} for s in seasons])


@api_bp.route("/seasons/<int:season_id>/teams")
def api_season_teams(season_id):
    teams = Team.query.filter_by(season_id=season_id).order_by(Team.name).all()
    return jsonify([{
        "id": t.id, "name": t.name, "code": t.code,
        "stadium": t.stadium or "", "city": t.city or "", "state": t.state or "",
        "mascot": t.mascot or "", "print_name": t.print_name or "",
        "abbreviation": t.abbreviation or "", "league": t.league or "",
        "division": t.division or "", "coach": t.coach or "", "conference": t.conference or "",
    } for t in teams])


@api_bp.route("/seasons/<int:season_id>/games")
def api_season_games(season_id):
    teams = Team.query.filter_by(season_id=season_id).all()
    team_ids = [t.id for t in teams]
    if not team_ids:
        return jsonify([])
    games = Game.query.filter(
        (Game.home_team_id.in_(team_ids)) | (Game.visitor_team_id.in_(team_ids))
    ).order_by(Game.date).all()
    results = []
    for g in games:
        results.append({
            "id": g.id,
            "date": g.date or "",
            "visitor": g.visitor_team.name if g.visitor_team else "",
            "home": g.home_team.name if g.home_team else "",
            "score": f"{g.visitor_runs}-{g.home_runs}" if g.is_complete else "",
            "is_complete": g.is_complete,
        })
    return jsonify(results)


@api_bp.route("/teams/<int:team_id>/players")
def api_team_players(team_id):
    players = Player.query.filter_by(team_id=team_id).order_by(Player.uniform_number).all()
    return jsonify([{
        "id": p.id,
        "name": p.name,
        "uniform_number": p.uniform_number or "",
        "bats": p.bats or "",
        "throws": p.throws or "",
        "player_class": p.player_class or "",
    } for p in players])


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
