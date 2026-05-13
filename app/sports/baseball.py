"""
Baseball / Softball plugin.

The existing baseball/softball logic in ``app.xmlapi`` and ``app.routes._boxscore_data``
predates the plugin system, so this plugin is a thin adapter that delegates to
those well-tested implementations. New sport-agnostic features should live on
the SportPlugin interface, while diamond-specific behaviour stays in the
legacy modules.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.sports.base import SportPlugin


class DiamondPlugin(SportPlugin):
    sport_id = 1  # 1=Baseball, 11=Softball both use this
    name = "Diamond"
    xml_root = "bsgame"
    boxscore_template = "boxscore_print.html"
    statboxscore_template = "statboxscore.html"

    # ── XML ──────────────────────────────────────────────────────────────────
    def build_xml(self, game) -> str:
        from app.xmlapi import build_bsgame_xml
        return build_bsgame_xml(game)

    # ── Boxscore data (used by HTML/PDF/JSON) ────────────────────────────────
    def build_boxscore_data(self, game) -> Dict[str, Any]:
        from app.routes import _boxscore_data
        data = _boxscore_data(game)
        data.setdefault("sport_id", game.sport_id)
        data.setdefault("scoring_summary", self.scoring_summary(game))
        return data

    # JSON: just return the (already plain) boxscore data dict.
    def build_json(self, game) -> Dict[str, Any]:
        return self.build_boxscore_data(game)

    # ── Scoring summary (baseball/softball) ──────────────────────────────────
    def scoring_summary(self, game) -> List[Dict[str, Any]]:
        """Distill scoring rows from the inning-by-inning line score.
        One row per half-inning in which a run was scored."""
        rows: List[Dict[str, Any]] = []
        vis_name = game.visitor_team.name if game.visitor_team else "Visitor"
        home_name = game.home_team.name if game.home_team else "Home"

        v_total = 0
        h_total = 0
        for inn in sorted(game.innings or [], key=lambda i: i.inning):
            try:
                vs = int(str(inn.visitor_score).replace("X", "0") or 0)
            except (TypeError, ValueError):
                vs = 0
            try:
                hs = int(str(inn.home_score).replace("X", "0") or 0)
            except (TypeError, ValueError):
                hs = 0
            if vs > 0:
                v_total += vs
                rows.append({
                    "prd": inn.inning,
                    "time": "",
                    "team": vis_name,
                    "text": f"{vis_name} scored {vs} run{'s' if vs != 1 else ''} in the top of the {_ord(inn.inning)}.",
                    "vscore": v_total,
                    "hscore": h_total,
                })
            if hs > 0:
                h_total += hs
                rows.append({
                    "prd": inn.inning,
                    "time": "",
                    "team": home_name,
                    "text": f"{home_name} scored {hs} run{'s' if hs != 1 else ''} in the bottom of the {_ord(inn.inning)}.",
                    "vscore": v_total,
                    "hscore": h_total,
                })
        return rows


def _ord(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
