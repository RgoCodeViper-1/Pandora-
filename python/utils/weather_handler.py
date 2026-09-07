"""
utils/weather_handler.py
=========================
Weather lookup execution worker.

Invoked by core/registry.py for "weather_query" / "weather_query_default".
Scrapes Google's weather snippet — no API key required. Swap this out for
a proper weather API provider by editing only `_fetch_from_google`.
"""

from utils.decorators import safe_execute, timed
from utils.file_utils import load_config
from utils.util_response import send_response

try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPER_AVAILABLE = True
except ImportError:
    SCRAPER_AVAILABLE = False

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}


def _default_location() -> str:
    cfg = load_config()
    return cfg.get("settings", {}).get("weather_location", "current location")


@timed
def _fetch_from_google(location: str) -> str | None:
    search_url = f"https://www.google.com/search?q=weather+{location.replace(' ', '+')}&hl=en"
    response = requests.get(search_url, headers=_HEADERS, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    temp = soup.find("span", {"id": "wob_tm"}) or soup.find("span", class_="wob_t")
    condition = soup.find("span", {"id": "wob_dc"})
    place = soup.find("div", {"id": "wob_loc"})

    if not (temp and condition):
        return None

    place_name = place.text if place else location
    return f"The weather in {place_name} is {condition.text} at {temp.text} degrees Celsius."


@safe_execute("Sorry, I couldn't fetch the weather right now.")
def fetch_weather(e: dict) -> str:
    """Handle 'weather_query' (has a 'location' slot) and
    'weather_query_default' (falls back to the configured default)."""
    location = (e.get("location") or "").strip() or _default_location()

    if not SCRAPER_AVAILABLE:
        return send_response("Weather lookup isn't available — missing requests/beautifulsoup4.")

    result = _fetch_from_google(location)
    if result is None:
        return send_response(f"I couldn't find weather details for {location}.")
    return send_response(result)
