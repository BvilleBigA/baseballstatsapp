# Gameday Stats

A self-hosted baseball and softball statistics web application. Import game data from Gameday Stats / DakStats XML files, run live score entry, and view league schedules, box scores, and play-by-play.

## Features

- **Seasons & events** — schedules, lineups, and game management
- **Live stat entry** — browser-based scoring aligned with Gameday Stats workflows
- **XML import** — Gameday Stats–format game files
- **Full box scores** — line scores, batting, pitching, fielding
- **Play-by-play** with pitch sequences
- **Computed stats** — AVG, OBP, SLG, OPS, ERA, WHIP, FPCT
- **REST API** for programmatic access
- **Responsive** admin UI

## Quick Start (Mac)

```bash
# 1. Clone the repo (folder name can be anything; this matches the product name)
git clone <repo-url> "Gameday Stats"
cd "Gameday Stats"

# 2. Set up Python virtual environment and install dependencies
make setup

# 3. Run the development server
make dev
```

Open http://localhost:5001 in your browser.

## Production Deployment (Home Server)

```bash
# Run with gunicorn (4 workers)
make prod
```

This starts the server on `0.0.0.0:5000`, accessible from your local network.

### Making it accessible from the internet

Options (pick one):

1. **Port forwarding** — Forward port 5001 on your router to your Mac's local IP
2. **Cloudflare Tunnel (recommended)** — Free, no port forwarding needed:
   ```bash
   brew install cloudflared
   cloudflared tunnel --url http://localhost:5001
   ```
3. **Tailscale** — VPN-based access from anywhere without exposing ports

### Running as a background service on macOS

Create `~/Library/LaunchAgents/com.gamedaystats.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gamedaystats</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/Gameday Stats/venv/bin/gunicorn</string>
        <string>-w</string>
        <string>4</string>
        <string>-b</string>
        <string>0.0.0.0:5000</string>
        <string>app:create_app()</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/Gameday Stats</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.gamedaystats.plist
```

## Usage

1. **Sign in** — Log in to open the Gameday Stats dashboard (scores, seasons, checklist).
2. **Seasons & games** — Create or select a season, add events, and enter lineups.
3. **Score games** — Launch stat entry for an event from the game detail page.
4. **Box scores** — View printable box scores and stat history from each game.

## API Endpoints

- `GET /api/leagues` — List all leagues
- `GET /api/teams/<id>/batting` — Team batting stats
- `GET /api/teams/<id>/pitching` — Team pitching stats

## Tech Stack

- Python 3 / Flask
- SQLite (via SQLAlchemy)
- Gunicorn (production)
- Gameday Stats (GWT) stat entry bundle + custom Flask templates
