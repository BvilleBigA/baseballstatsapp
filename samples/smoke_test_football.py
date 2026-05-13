"""End-to-end smoke test: seed a football game from the sample blob and hit
every output endpoint (HTML, JSON, XML, PDF x 3 styles + per-quarter PBP)."""
import sys
sys.path.insert(0, r'c:\Users\Administrator\Documents\gamedaystats')

from app import create_app, db
from app.models import User, School, Season, Team, Game

BLOB_PATH = r'c:\Users\Administrator\Downloads\boxscore_20260512_0913.json'

app = create_app()
with app.app_context():
    blob = open(BLOB_PATH, encoding='utf-8').read()

    season = Season.query.filter_by(sport_id=0).first()
    if not season:
        season = Season(name='FB Smoke Test', sport_id=0, sport_code='fball',
                        gender='male', play_entry_mode='pbp_simple')
        db.session.add(season)
        db.session.flush()

    school = School.query.first() or School(name='Test School')
    if not school.id:
        db.session.add(school)
        db.session.flush()

    vis = Team.query.filter_by(season_id=season.id, code='STATS1').first()
    if not vis:
        vis = Team(code='STATS1', team_id='STATS1', name='Stats1',
                   season_id=season.id, school_id=school.id, abbreviation='STATS1')
        db.session.add(vis)
        db.session.flush()

    hom = Team.query.filter_by(season_id=season.id, code='STATS2').first()
    if not hom:
        hom = Team(code='STATS2', team_id='STATS2', name='Stats2',
                   season_id=season.id, school_id=school.id, abbreviation='STATS2')
        db.session.add(hom)
        db.session.flush()

    g = Game.query.filter_by(visitor_team_id=vis.id, home_team_id=hom.id).first()
    if not g:
        g = Game(date='2025-05-29', location='The St. James', start_time='8:00 PM',
                 visitor_team_id=vis.id, home_team_id=hom.id, scheduled_innings=4,
                 is_complete=True, has_lineup=True, scorer='Try Prestostats')
        db.session.add(g)
        db.session.flush()
    g.gwt_bs_blob = blob
    g.is_complete = True
    g.has_lineup = True
    g.visitor_runs = 35
    g.home_runs = 17
    db.session.commit()
    eid = g.id
    print('Football smoke game id:', eid, 'season.sport_id=', season.sport_id)
    print('game.sport_id via property =', g.sport_id)

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
            f'/game/{eid}/statboxscore',
            f'/game/{eid}/statboxscore.json',
            f'/game/{eid}/boxscore.xml',
            f'/game/{eid}/boxscore.pdf?style=full',
            f'/game/{eid}/boxscore.pdf?style=summary',
            f'/game/{eid}/boxscore.pdf?style=pbp&qtr=1',
            f'/game/{eid}/boxscore.pdf?style=pbp&qtr=2',
            f'/game/{eid}/boxscore.pdf?style=pbp&qtr=3',
            f'/game/{eid}/boxscore.pdf?style=pbp&qtr=4',
            f'/action/stats/downloadXML.jsp?evt={eid}',
            f'/action/stats/download.jspd?evt={eid}&style=summary',
        ]
        for url in urls:
            r = c.get(url, follow_redirects=False)
            ctype = (r.headers.get('Content-Type') or '')[:32]
            loc = r.headers.get('Location') or ''
            print(f'{url:60s} -> {r.status_code:3d}  len={len(r.data):7d}  {ctype}  {loc}')
            if r.status_code >= 500:
                print('--- response body excerpt ---')
                print(r.data[:1600].decode('utf-8', errors='replace'))
                print('--- end ---')
