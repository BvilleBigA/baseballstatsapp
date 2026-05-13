"""
Baseball / softball Sport handler.

Delegates to the long-standing implementations already in:
  - app.xmlapi.build_bsgame_xml
  - app.routes._boxscore_data / stat_boxscore / stat_boxscore_pdf

This wrapper exists so app.sports.get_sport(...) returns a consistent
interface for every sport; the actual baseball/softball code is unchanged.
"""
from flask import render_template, redirect, url_for

from app.sports.base import Sport


class BaseballSport(Sport):
    """Handler for sport_id=1 (Baseball) and sport_id=11 (Softball)."""

    def __init__(self, sport_id=1, name='Baseball'):
        self.sport_id = sport_id
        self.name = name

    # status_options is owned by app/routes.py (already wired up there).

    def boxscore_data(self, game):
        from app.routes import _boxscore_data
        return _boxscore_data(game)

    def render_html(self, game, **ctx):
        from app.routes import _boxscore_data
        data = _boxscore_data(game)
        return render_template('statboxscore.html', game=game, data=data, **ctx)

    def render_pdf(self, game, style='full', **ctx):
        from app.routes import _boxscore_data
        data = _boxscore_data(game)
        return render_template('boxscore_print.html', game=game, data=data, **ctx)

    def build_xml(self, game):
        from app.xmlapi import build_bsgame_xml
        return build_bsgame_xml(game)

    def persist_save(self, game, bs, statuscode=-2, live_stats_raw=''):
        # Diamond sports use the original heavyweight persistence path.
        from app.gwtapi import _persist_boxscore_full
        _persist_boxscore_full(game, bs, statuscode=statuscode, live_stats_raw=live_stats_raw)
