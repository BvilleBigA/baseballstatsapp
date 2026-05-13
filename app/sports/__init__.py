"""
Sport plugin registry.

Each sport (Football, Baseball, Softball, Basketball, ...) provides a SportPlugin
that knows how to produce XML, JSON, boxscore data, and HTML/PDF templates from
a Game model.  The Flask routes dispatch on `Game.sport_id` so adding a new
sport is a matter of writing one plugin file and registering it here.

Sport_id reference (matches Season.sport_id):
    0  = Football       (FootballPlugin)
    1  = Baseball       (DiamondPlugin)
    2  = Basketball
    3  = Soccer
    4  = Volleyball
    5  = Ice Hockey
    6  = Lacrosse (M)
    7  = Tennis
    9  = Field Hockey
    10 = Lacrosse (W)
    11 = Softball       (DiamondPlugin)
    12 = Water Polo
"""

from app.sports.base import SportPlugin
from app.sports.baseball import DiamondPlugin
from app.sports.football import FootballPlugin


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY = {
    0:  FootballPlugin(),
    1:  DiamondPlugin(),
    11: DiamondPlugin(),
}

_DIAMOND = _REGISTRY[1]


def get_plugin_for(game) -> SportPlugin:
    """Return the SportPlugin for the given Game, falling back to diamond sports.
    Note: sport_id 0 is Football, so we cannot ``or 1`` the value."""
    sid = getattr(game, "sport_id", 1)
    if sid is None:
        sid = 1
    return _REGISTRY.get(int(sid), _DIAMOND)


def get_plugin_for_sport_id(sport_id) -> SportPlugin:
    """Direct lookup by sport_id (defaults to diamond)."""
    if sport_id is None:
        sport_id = 1
    return _REGISTRY.get(int(sport_id), _DIAMOND)


__all__ = ["SportPlugin", "get_plugin_for", "get_plugin_for_sport_id"]
