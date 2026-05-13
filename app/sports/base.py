"""
Sport base class.

Sport handlers translate a `Game` (and its `gwt_bs_blob` from the GWT entry app)
into the four output formats the app supports for every sport:

    HTML        — live, in-app boxscore page (matches statboxscore.html)
    JSON        — machine-readable boxscore (matches Presto's boxscore_*.json)
    XML         — Presto-format XML download (matches downloadXML.xml)
    PDF (HTML)  — print-friendly HTML page the browser turns into a PDF

A handler can override only what it needs; methods left as defaults raise
NotImplementedError so an in-progress sport fails loudly instead of silently
serving an empty page.
"""
from __future__ import annotations

import json as _json
from flask import jsonify, render_template


class Sport:
    """Abstract base. Override per-sport."""
    sport_id: int = -1
    name: str = 'Unknown'

    # ── Stat-entry status dropdown ────────────────────────────────────────
    def status_options(self):
        return []

    # ── Boxscore data (JSON-friendly) ─────────────────────────────────────
    def boxscore_data(self, game):
        raise NotImplementedError(f'{self.name}.boxscore_data not implemented')

    # ── HTTP responses ────────────────────────────────────────────────────
    def render_json(self, game):
        return jsonify(self.boxscore_data(game))

    def render_html(self, game, **ctx):
        raise NotImplementedError(f'{self.name}.render_html not implemented')

    def render_pdf(self, game, style='full', **ctx):
        raise NotImplementedError(f'{self.name}.render_pdf not implemented')

    def build_xml(self, game):
        raise NotImplementedError(f'{self.name}.build_xml not implemented')

    # ── Persistence hook (called from GWT save endpoints) ────────────────
    def persist_save(self, game, bs, statuscode=-2, live_stats_raw=''):
        """Persist a GWT save payload. Default: store the blob + line score only."""
        from app import db
        from app.models import InningScore
        game.gwt_bs_blob = _json.dumps(bs) if isinstance(bs, dict) else (bs or '')
        # Pull a basic line score so the schedule/listing pages have something to show
        try:
            self._sync_line_score_from_periodstats(game, bs)
        except Exception:
            pass
        if statuscode == 0:
            game.is_complete = True

    # ── Helpers shared across non-diamond sports ─────────────────────────
    @staticmethod
    def _sync_line_score_from_periodstats(game, bs):
        """Update Game.visitor_runs/home_runs + InningScore rows from periodstats."""
        from app import db
        from app.models import InningScore

        if not isinstance(bs, dict):
            return
        teams = bs.get('teams') or []
        if len(teams) < 2:
            return
        vis_periods = teams[0].get('periodstats') or []
        home_periods = teams[1].get('periodstats') or []

        def _int(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        vis_total = sum(_int(p.get('score')) for p in vis_periods if _int(p.get('score')) != 99)
        home_total = sum(_int(p.get('score')) for p in home_periods if _int(p.get('score')) != 99)
        game.visitor_runs = vis_total
        game.home_runs = home_total
        game.has_lineup = game.has_lineup or any(
            bool(p.get('participated')) or _int(p.get('readOrder')) > 0 or _int(p.get('uniform')) >= 0
            for t in teams for p in (t.get('players') or [])
        )

        existing = {i.inning: i for i in game.innings}
        nperiods = max(len(vis_periods), len(home_periods))
        for i in range(nperiods):
            n = i + 1
            v = _int(vis_periods[i].get('score')) if i < len(vis_periods) else 0
            h = _int(home_periods[i].get('score')) if i < len(home_periods) else 0
            row = existing.get(n)
            if row is None:
                if v == 0 and h == 0:
                    continue
                db.session.add(InningScore(
                    game_id=game.id, inning=n,
                    visitor_score=('X' if v == 99 else str(v)),
                    home_score=('X' if h == 99 else str(h)),
                ))
            else:
                row.visitor_score = 'X' if v == 99 else str(v)
                row.home_score = 'X' if h == 99 else str(h)

        # Trim trailing 0-0 rows that aren't real
        for n in sorted(existing.keys(), reverse=True):
            if n > nperiods and (existing[n].visitor_score in ('0', '') and
                                  existing[n].home_score in ('0', '')):
                db.session.delete(existing[n])


class GenericBlobSport(Sport):
    """Fallback handler used for sports we haven't fleshed out yet.

    Stores the raw GWT blob, returns it verbatim for JSON, and gives the
    user a friendly message for HTML/PDF/XML. This keeps the app from
    500'ing the moment a season is created for a new sport_id.
    """
    sport_id = -1
    name = 'Generic'

    def boxscore_data(self, game):
        try:
            blob = _json.loads(game.gwt_bs_blob) if game.gwt_bs_blob else {}
        except (ValueError, TypeError):
            blob = {}
        return blob

    def render_html(self, game, **ctx):
        return render_template('boxscore_unsupported.html',
                               game=game, sport_id=getattr(game, 'sport_id', None),
                               **ctx)

    def render_pdf(self, game, style='full', **ctx):
        return self.render_html(game, **ctx)

    def build_xml(self, game):
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<game sport_id="{getattr(game, "sport_id", "")}" '
            f'event_id="{game.id}" '
            'message="XML export not yet available for this sport."></game>\n'
        )
