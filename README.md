# Baseball/Softball Stats App

A self-hosted baseball and softball statistics tracking web application. Import game data from PrestoSports/DakStats XML files and view league standings, team stats, player stats, box scores, and play-by-play.

## Features

- **Multi-team league** support with standings
- **XML game import** — upload PrestoSports-format game files
- **Full box scores** — line scores, batting, pitching, fielding
- **Player pages** — season totals, game log
- **Play-by-play** with pitch sequences
- **Computed stats** — AVG, OBP, SLG, OPS, ERA, WHIP, FPCT
- **REST API** for programmatic access
- **Mobile-friendly** responsive design

## Quick Start (Mac)

```bash
# 1. Clone the repo
git clone <repo-url> && cd baseballstatsapp

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

Create `~/Library/LaunchAgents/com.baseballstats.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.baseballstats</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/baseballstatsapp/venv/bin/gunicorn</string>
        <string>-w</string>
        <string>4</string>
        <string>-b</string>
        <string>0.0.0.0:5000</string>
        <string>app:create_app()</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/baseballstatsapp</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.baseballstats.plist
```

## Usage

1. **Create a league** — Click "Create League" on the home page
2. **Upload game XML** — Click "Upload Game" and select your PrestoSports XML file(s)
3. **Browse stats** — View standings, click into teams, players, and box scores

## API Endpoints

- `GET /api/leagues` — List all leagues
- `GET /api/teams/<id>/batting` — Team batting stats
- `GET /api/teams/<id>/pitching` — Team pitching stats

## Tech Stack

- Python 3 / Flask
- SQLite (via SQLAlchemy)
- Gunicorn (production)
- No JavaScript frameworks — vanilla HTML/CSS/JS
