"""
utils/browser_handler.py
=========================
Website / browser navigation execution worker.

Invoked by core/registry.py for the "browser_open_known", "browser_open_url",
and "browser_open_generic" intents.
"""

import re
import webbrowser
from difflib import get_close_matches

from utils.decorators import safe_execute
from utils.util_response import send_response

KNOWN_SITES = {
    "youtube":        "https://www.youtube.com",
    "wikipedia":      "https://www.wikipedia.org",
    "gmail":          "https://mail.google.com/mail/u/0/#inbox",
    "calendar":       "https://calendar.google.com/calendar/u/0/r",
    "github":         "https://github.com",
    "stack overflow": "https://stackoverflow.com",
    "google":         "https://www.google.com",
    "reddit":         "https://www.reddit.com",
    "spotify":        "https://open.spotify.com",
}

_URL_PATTERN = re.compile(r"^(https?://)?([\w\-]+\.)+[\w\-]+(/[\w\-./?%&=]*)?$")


def _is_valid_url(text: str) -> bool:
    return bool(_URL_PATTERN.match(text.strip()))


@safe_execute("I couldn't open that site.")
def open_site(e: dict) -> str:
    """
    Resolve and open a browser target from the matched intent's entities.

    Covers all three browser intents:
      - browser_open_known   → e['site']
      - browser_open_url     → e['url']
      - browser_open_generic → neither slot present
    Falls back to a Google search when nothing resolves cleanly.
    """
    target = (e.get("url") or e.get("site") or "").strip()
    if not target:
        webbrowser.open(KNOWN_SITES["google"])
        return send_response("Opening the browser.")

    # 1. Direct known-site match
    for name, site_url in KNOWN_SITES.items():
        if name in target.lower():
            webbrowser.open(site_url)
            return send_response(f"Opening {name.title()}.")

    # 2. Already a valid URL/domain
    if _is_valid_url(target):
        full_url = target if target.startswith("http") else f"https://{target}"
        webbrowser.open(full_url)
        return send_response(f"Opening {full_url}.")

    # 3. Fuzzy match against known sites
    close = get_close_matches(target.lower(), KNOWN_SITES.keys(), n=1, cutoff=0.6)
    if close:
        webbrowser.open(KNOWN_SITES[close[0]])
        return send_response(f"Opening {close[0].title()}.")

    # 4. Fall back to a Google search for the phrase
    webbrowser.open(f"https://www.google.com/search?q={target}")
    return send_response(f"I couldn't identify '{target}' as a site — searching instead.")
