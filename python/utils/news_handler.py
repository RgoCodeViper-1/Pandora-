"""
utils/news_handler.py
======================
News headlines execution worker.

Invoked by core/registry.py for "news_headlines".

Fixes vs. the legacy version:
  - Removed the import of `utils.util_handler` (doesn't exist) — API keys
    and settings now load from shared/config/configuration.json via
    utils.file_utils, per project convention.
  - Provider availability (newsapi / feedparser) is now detected locally
    instead of being imported as flags from the missing module.
"""

from utils.decorators import safe_execute
from utils.file_utils import get_api_key, load_config
from utils.util_response import send_response

try:
    from newsapi import NewsApiClient
    NEWSAPI_AVAILABLE = True
except ImportError:
    NEWSAPI_AVAILABLE = False

try:
    import feedparser
    RSS_AVAILABLE = True
except ImportError:
    RSS_AVAILABLE = False

RSS_FALLBACK_URL = "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"


def _default_location() -> str:
    cfg = load_config()
    return cfg.get("settings", {}).get("weather_location", "India")


@safe_execute("I couldn't fetch the news right now.")
def fetch_news(e: dict) -> str:
    """Fetch top headlines via NewsAPI, falling back to an RSS feed."""
    location = (e.get("location") or _default_location()).split(",")[0]
    max_headlines = int(e.get("count", 5))

    news_api_key = get_api_key("news_api")

    if news_api_key and NEWSAPI_AVAILABLE:
        try:
            client = NewsApiClient(api_key=news_api_key)
            result = client.get_top_headlines(q=location, language="en", country="in")
            articles = result.get("articles", [])[:max_headlines]
            if articles:
                for a in articles:
                    print(f"- {a['title']}")
                return send_response(f"Here are the top headlines for {location}.")
        except Exception:
            pass  # fall through to RSS

    if RSS_AVAILABLE:
        try:
            feed = feedparser.parse(RSS_FALLBACK_URL)
            entries = feed.entries[:max_headlines]
            if entries:
                for entry in entries:
                    print(f"- {entry.title}")
                return send_response("Here are the top headlines.")
        except Exception:
            pass

    return send_response("I'm unable to retrieve the news at this moment.")
