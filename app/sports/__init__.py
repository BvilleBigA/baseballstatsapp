"""
Sport plugin registry.

Each sport handler implements `app.sports.base.Sport` and is registered here by
`sport_id` (the integer used in `Season.sport_id`). The handler centralizes:

  - status_options()       — labels for the GWT status dropdown
  - boxscore_data(game)    — JSON-friendly dict shared by HTML / JSON / PDF
  - build_xml(game)        — Presto-format XML payload as a string
  - render_html(...)       — live in-app boxscore page
  - render_pdf(...)        — print-friendly HTML used as "Save as PDF"
  - persist_save(game,bs)  — store the GWT save payload (skips diamond-sport SQL
                             plumbing for sports that live entirely in gwt_bs_blob)

Adding a new sport: implement Sport in app/sports/<sport>.py and register it
below with the matching sport_id.
"""
from app.sports.base import Sport, GenericBlobSport
from app.sports.baseball import BaseballSport
from app.sports.football import FootballSport

# sport_id -> Sport handler. See models.Season.sport_id for the canonical IDs.
_REGISTRY = {
    0:  FootballSport(),
    1:  BaseballSport(sport_id=1, name='Baseball'),
    11: BaseballSport(sport_id=11, name='Softball'),
}

# Single fallback handler reused for every sport that hasn't been built out yet.
# It stores the raw GWT blob, derives the line score, and returns the blob as JSON.
_FALLBACK = GenericBlobSport()


def get_sport(sport_id):
    """Return the Sport handler for the given sport_id, or a generic fallback."""
    try:
        sid = int(sport_id)
    except (TypeError, ValueError):
        sid = 1
    return _REGISTRY.get(sid, _FALLBACK)


def get_sport_for_game(game):
    """Convenience: look up the handler for a Game by its season's sport_id."""
    if game is None:
        return _FALLBACK
    return get_sport(getattr(game, 'sport_id', 1))


__all__ = ['Sport', 'get_sport', 'get_sport_for_game']
