"""
Sport plugin interface.

A SportPlugin is the single point of customisation per sport. Every sport
exposes the same set of capabilities (XML, JSON, boxscore data, scoring
summary, play-by-play, HTML/PDF templates) so the rest of the application
can stay sport-agnostic.

Implementations should be safe to call on incomplete games (return empty
collections rather than raising).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SportPlugin:
    # Identification ----------------------------------------------------------
    sport_id: int = -1
    name: str = "Generic"
    xml_root: str = "game"   # e.g. 'bsgame', 'fbgame'

    # Templates ---------------------------------------------------------------
    boxscore_template: str = "boxscore_print.html"     # HTML / print-to-PDF
    statboxscore_template: str = "statboxscore.html"   # live in-app view

    # ── Data builders (override per sport) ───────────────────────────────────

    def build_boxscore_data(self, game) -> Dict[str, Any]:
        """Return a dict for boxscore templates. Sport-shaped (linescore by
        inning vs quarter, batting vs passing/rushing/receiving tables, …).
        Required keys (all sports):
            visitor_name, home_name, visitor_runs, home_runs,
            date, start_time, status_label, location,
            visitor_record, home_record,
            scoring_summary  -> list of {prd, time, team, text, vscore, hscore}
            sport_id
        """
        raise NotImplementedError

    def build_xml(self, game) -> str:
        """Return a UTF-8 XML string in the canonical Presto-style schema."""
        raise NotImplementedError

    def build_json(self, game) -> Dict[str, Any]:
        """Return a JSON-serialisable dict. Default returns boxscore_data."""
        return self.build_boxscore_data(game)

    def scoring_summary(self, game) -> List[Dict[str, Any]]:
        """Return a list of scoring events.
        Each entry: {prd, time, team, text, vscore, hscore}
        """
        return []

    def play_by_play(self, game) -> List[Dict[str, Any]]:
        """Return list of {period, text, time, team, score_v, score_h} entries."""
        return []

    # Hint used by the boxscore route to pick the right HTML template
    def boxscore_view_kwargs(self, game) -> Dict[str, Any]:
        """Extra kwargs passed to render_template along with `data`."""
        return {}
