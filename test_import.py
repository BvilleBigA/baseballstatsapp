"""Quick smoke test: create app, import the sample XML, verify data."""

import sys
import os

# Save the sample XML inline for testing
SAMPLE_XML = open(os.path.join(os.path.dirname(__file__), "sample_game.xml"), "rb").read()

from app import create_app, db
from app.models import League, Team, Player, Game, BattingStats, PitchingStats, Play
from app.xml_parser import parse_game_xml

app = create_app()

with app.app_context():
    db.drop_all()
    db.create_all()

    # Create a league
    league = League(name="Test League", sport="softball", season="2025")
    db.session.add(league)
    db.session.commit()

    # Parse the XML
    game = parse_game_xml(SAMPLE_XML, league)
    assert game is not None, "Game was not created"

    # Verify teams
    teams = Team.query.all()
    print(f"Teams: {len(teams)}")
    assert len(teams) == 2, f"Expected 2 teams, got {len(teams)}"
    for t in teams:
        print(f"  - {t.name} (code={t.code})")

    # Verify game
    games = Game.query.all()
    print(f"Games: {len(games)}")
    assert len(games) == 1
    g = games[0]
    print(f"  {g.visitor_team.name} {g.visitor_runs} @ {g.home_team.name} {g.home_runs}")
    assert g.visitor_runs == 0
    assert g.home_runs == 5
    assert g.is_complete

    # Verify players
    players = Player.query.all()
    print(f"Players: {len(players)}")

    # Verify batting stats
    batting = BattingStats.query.all()
    print(f"Batting stat lines: {len(batting)}")
    assert len(batting) > 0

    # Verify pitching stats
    pitching = PitchingStats.query.all()
    print(f"Pitching stat lines: {len(pitching)}")
    assert len(pitching) > 0

    # Verify plays
    plays = Play.query.all()
    print(f"Plays: {len(plays)}")
    assert len(plays) > 0

    # Verify duplicate import protection
    game2 = parse_game_xml(SAMPLE_XML, league)
    games_after = Game.query.all()
    assert len(games_after) == 1, "Duplicate game was created!"
    print("Duplicate protection: OK")

    # Test a specific player
    huffman = Player.query.filter_by(name="A. Huffman").first()
    assert huffman is not None
    h_batting = BattingStats.query.filter_by(player_id=huffman.id).first()
    print(f"  A. Huffman: {h_batting.h}/{h_batting.ab}, {h_batting.rbi} RBI, {h_batting.doubles} 2B")
    assert h_batting.h == 3
    assert h_batting.ab == 3
    assert h_batting.rbi == 3
    assert h_batting.doubles == 1

    print("\nAll tests passed!")
