"""Regression smoke test: existing baseball/softball endpoints still work
after the sport-dispatch refactor."""
import sys
sys.path.insert(0, r'c:\Users\Administrator\Documents\gamedaystats')

from app import create_app, db
from app.models import User, Game, Season

app = create_app()
with app.app_context():
    # Find any non-football game from the production DB
    bb_game = (Game.query
               .join(Game.visitor_team)
               .filter(Game.visitor_team_id.isnot(None))
               .first())
    if bb_game is None:
        print('No games in DB to test — skipping')
        sys.exit(0)

    print(f'Game id={bb_game.id} sport_id={bb_game.sport_id}'
          f' is_complete={bb_game.is_complete} has_lineup={bb_game.has_lineup}'
          f' status="{bb_game.status_label}"')

    admin = User.query.filter_by(role='admin').first()
    if not admin:
        admin = User(username='admin@local', password_sha256='0' * 64,
                     role='admin', display_name='Smoke')
        db.session.add(admin)
        db.session.commit()

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_id'] = admin.id

        urls = [
            f'/game/{bb_game.id}/statboxscore',
            f'/game/{bb_game.id}/statboxscore.json',
            f'/game/{bb_game.id}/boxscore.xml',
            f'/game/{bb_game.id}/boxscore.pdf',
        ]
        for url in urls:
            r = c.get(url, follow_redirects=False)
            ctype = (r.headers.get('Content-Type') or '')[:32]
            print(f'{url:60s} -> {r.status_code:3d}  len={len(r.data):7d}  {ctype}')
            if r.status_code >= 500:
                print('--- response body excerpt ---')
                print(r.data[:1500].decode('utf-8', errors='replace'))
                print('--- end ---')
