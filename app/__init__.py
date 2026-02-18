from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_app(config_class=None):
    app = Flask(__name__)

    if config_class:
        app.config.from_object(config_class)
    else:
        from config import Config
        app.config.from_object(Config)

    db.init_app(app)

    from app.routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    with app.app_context():
        from app import models  # noqa: F401
        db.create_all()
        _seed_demo_data()

    return app


def _seed_demo_data():
    """Create a Demo Season with sample teams, rosters, and games if it doesn't exist."""
    from app.models import Season, Team, Player, Game, InningScore, BattingStats, PitchingStats

    if Season.query.filter_by(name="Demo Season").first():
        return

    season = Season(name="Demo Season", play_entry_mode="box_game_totals", rules="rules_hs_sb", gender="female")
    db.session.add(season)
    db.session.flush()

    # --- Teams ---
    teams_data = [
        {"code": "EAGLE", "name": "Eagles", "abbreviation": "EGL", "mascot": "Eagle", "city": "Springfield", "state": "IL", "stadium": "Eagle Field", "coach": "Coach Smith", "league": "Central", "division": "East", "conference": "Metro"},
        {"code": "TIGER", "name": "Tigers", "abbreviation": "TGR", "mascot": "Tiger", "city": "Riverside", "state": "CA", "stadium": "Tiger Park", "coach": "Coach Johnson", "league": "Central", "division": "East", "conference": "Metro"},
        {"code": "HAWK", "name": "Hawks", "abbreviation": "HWK", "mascot": "Hawk", "city": "Lakewood", "state": "OH", "stadium": "Hawk Stadium", "coach": "Coach Davis", "league": "Central", "division": "West", "conference": "Metro"},
        {"code": "BEAR", "name": "Bears", "abbreviation": "BRS", "mascot": "Bear", "city": "Fairview", "state": "TX", "stadium": "Bear Diamond", "coach": "Coach Wilson", "league": "Central", "division": "West", "conference": "Metro"},
    ]
    teams = []
    for td in teams_data:
        t = Team(season_id=season.id, print_name=td["name"], **td)
        db.session.add(t)
        teams.append(t)
    db.session.flush()

    # --- Rosters ---
    positions = ["P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DP", "EF", "P", "P"]
    first_names = [
        ["Emma", "Olivia", "Ava", "Sophia", "Isabella", "Mia", "Charlotte", "Amelia", "Harper", "Evelyn", "Abigail", "Lily", "Grace"],
        ["Madison", "Chloe", "Ella", "Riley", "Zoey", "Nora", "Hazel", "Layla", "Penelope", "Scarlett", "Aria", "Luna", "Stella"],
        ["Brooklyn", "Savannah", "Claire", "Skylar", "Paisley", "Audrey", "Bella", "Ellie", "Anna", "Natalie", "Caroline", "Quinn", "Ruby"],
        ["Addison", "Leah", "Aubrey", "Jade", "Vivian", "Willow", "Madelyn", "Eleanor", "Piper", "Rylee", "Mackenzie", "Faith", "Kinley"],
    ]
    last_names = [
        ["Anderson", "Baker", "Clark", "Davis", "Evans", "Foster", "Garcia", "Harris", "Irwin", "Jones", "Kelly", "Lopez", "Miller"],
        ["Nelson", "Owens", "Perez", "Quinn", "Roberts", "Scott", "Taylor", "Underwood", "Vasquez", "Walker", "Young", "Adams", "Brown"],
        ["Carter", "Dixon", "Edwards", "Fisher", "Grant", "Hayes", "Jackson", "King", "Lewis", "Morgan", "Nash", "Oliver", "Parker"],
        ["Reed", "Stone", "Thomas", "Upton", "Vega", "White", "Xiong", "York", "Zimmerman", "Allen", "Brooks", "Collins", "Drake"],
    ]

    all_players = {}
    for ti, team in enumerate(teams):
        team_players = []
        for pi in range(13):
            p = Player(
                name=f"{first_names[ti][pi]} {last_names[ti][pi]}",
                first_name=first_names[ti][pi],
                last_name=last_names[ti][pi],
                uniform_number=str(pi + 1).zfill(2),
                position=positions[pi],
                bats="Right" if pi % 3 == 0 else ("Left" if pi % 3 == 1 else "Switch"),
                throws="Right" if pi % 2 == 0 else "Left",
                year=["Fr", "So", "Jr", "Sr"][pi % 4],
                height=f"5'{4 + (pi % 8)}\"",
                weight=str(120 + pi * 5),
                hometown=team.city,
                team_id=team.id,
            )
            db.session.add(p)
            team_players.append(p)
        all_players[team.id] = team_players
    db.session.flush()

    # --- Games ---
    import itertools
    matchups = list(itertools.combinations(range(4), 2))
    game_dates = ["2025-03-01", "2025-03-08", "2025-03-15", "2025-03-22", "2025-03-29", "2025-04-05"]
    import random
    random.seed(42)

    for gi, (vi, hi) in enumerate(matchups):
        visitor = teams[vi]
        home = teams[hi]
        v_runs = random.randint(0, 8)
        h_runs = random.randint(0, 8)
        while v_runs == h_runs:
            h_runs = random.randint(0, 8)
        v_hits = v_runs + random.randint(1, 4)
        h_hits = h_runs + random.randint(1, 4)

        game = Game(
            date=game_dates[gi],
            location=home.city,
            stadium=home.stadium,
            start_time="4:00 PM",
            scheduled_innings=7,
            is_league_game=True,
            is_complete=True,
            visitor_team_id=visitor.id,
            home_team_id=home.id,
            visitor_runs=v_runs,
            visitor_hits=v_hits,
            visitor_errors=random.randint(0, 3),
            visitor_lob=random.randint(2, 8),
            home_runs=h_runs,
            home_hits=h_hits,
            home_errors=random.randint(0, 3),
            home_lob=random.randint(2, 8),
        )
        db.session.add(game)
        db.session.flush()

        # Inning scores
        v_inning_runs = _distribute_runs(v_runs, 7)
        h_inning_runs = _distribute_runs(h_runs, 7)
        for inning in range(1, 8):
            inn = InningScore(game_id=game.id, inning=inning,
                              visitor_score=str(v_inning_runs[inning - 1]),
                              home_score=str(h_inning_runs[inning - 1]))
            db.session.add(inn)

        # Batting stats for each team
        for side, team_obj, runs, hits in [
            ("visitor", visitor, v_runs, v_hits),
            ("home", home, h_runs, h_hits),
        ]:
            players = all_players[team_obj.id]
            hits_left = hits
            runs_left = runs
            for pi, player in enumerate(players[:9]):
                p_hits = min(hits_left, random.randint(0, 2))
                hits_left -= p_hits
                p_runs = min(runs_left, random.randint(0, 1)) if p_hits > 0 else 0
                runs_left -= p_runs
                ab = random.randint(max(p_hits, 1), 4)
                bs = BattingStats(
                    game_id=game.id, player_id=player.id, team_id=team_obj.id,
                    batting_order=pi + 1, position=player.position,
                    is_starter=True, ab=ab, r=p_runs, h=p_hits,
                    rbi=random.randint(0, p_hits),
                    bb=random.randint(0, 1), so=random.randint(0, 2),
                )
                db.session.add(bs)

        # Pitching stats
        for side, team_obj, opp_runs, opp_hits in [
            ("visitor", visitor, h_runs, h_hits),
            ("home", home, v_runs, v_hits),
        ]:
            pitcher = all_players[team_obj.id][0]  # first player is pitcher
            is_winner = (side == "visitor" and v_runs > h_runs) or (side == "home" and h_runs > v_runs)
            ps = PitchingStats(
                game_id=game.id, player_id=pitcher.id, team_id=team_obj.id,
                appear=1, gs=1, ip=7.0,
                h=opp_hits, r=opp_runs, er=max(0, opp_runs - random.randint(0, 1)),
                bb=random.randint(1, 4), so=random.randint(3, 10),
                bf=opp_hits + opp_runs + random.randint(20, 25),
                win=is_winner, loss=not is_winner,
            )
            db.session.add(ps)

    db.session.commit()


def _distribute_runs(total, innings):
    """Distribute runs randomly across innings."""
    import random
    result = [0] * innings
    for _ in range(total):
        result[random.randint(0, innings - 1)] += 1
    return result
