#!/usr/bin/env python3
"""
Poll a livestats XML URL and save the response to a local file every 1 second.
Use to test live updates on your team website.

Usage:
    python scripts/poll_livestats_xml.py "http://localhost:5001/action/stats/downloadXML.jsp?event_id=15"
    python scripts/poll_livestats_xml.py "http://localhost:5001/action/stats/downloadXML.jsp?event_id=15" -o livestats_xml/game_15.xml

Press Ctrl+C to stop.
"""

import argparse
import re
import sys
import time

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Poll livestats XML URL every 1 second")
    parser.add_argument("url", help="Full URL to the XML (e.g. .../downloadXML.jsp?event_id=15)")
    parser.add_argument("-o", "--output", help="Output file path (default: livestats_xml/game_<id>.xml)")
    parser.add_argument("-i", "--interval", type=float, default=1.0, help="Seconds between fetches (default: 1)")
    args = parser.parse_args()

    # Derive output path from URL if not provided
    output = args.output
    if not output:
        m = re.search(r"event_id=(\d+)|evt=(\d+)|id=(\d+)", args.url, re.I)
        eid = (m.group(1) or m.group(2) or m.group(3)) if m else "unknown"
        output = f"livestats_xml/game_{eid}.xml"

    print(f"Polling {args.url} every {args.interval}s → {output}")
    print("Press Ctrl+C to stop.\n")

    count = 0
    try:
        while True:
            try:
                r = requests.get(args.url, timeout=10)
                r.raise_for_status()
                with open(output, "wb") as f:
                    f.write(r.content)
                count += 1
                if count == 1 or count % 10 == 0:
                    print(f"[{count}] Saved {len(r.content):,} bytes → {output}")
            except requests.RequestException as e:
                print(f"Error: {e}", file=sys.stderr)
            except OSError as e:
                print(f"Write error: {e}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {count} fetch(es).")


if __name__ == "__main__":
    main()
