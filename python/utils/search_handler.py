# Modify these handler to strip away all the leagacy Jarvis code lines and function and only keep the core execution method tuned for the architecture of Pandora.
# Remove the listen() function and directly route it to the Pandora central speaking and listening files under speech/ folder - tts.py and stt.py. May also use the util handler for the bridge.
# Remove the honorifics
# For the data load and save, use the central shared/ folder for all the data storage, do not replicate for its dedicated to only the shared folder under python/shared/...


import webbrowser
import wikipedia
from utils.util_response import send_response


def search_google(query):
    """Direct Google search execution."""
    search_query = query.lower().replace("search google for", "").replace("search google", "").strip()
    send_response(f"Searching Google for {search_query}, Sir.")
    webbrowser.open(f"https://www.google.com/search?q={search_query}")

def search_wikipedia(query, sentences=2):
    """Fetch and speak summaries from Wikipedia."""
    search_query = query.lower().replace("search wikipedia for", "").replace("wikipedia", "").strip()
    send_response(f"Searching Wikipedia for {search_query}, Sir...")
    try:
        results = wikipedia.summary(search_query, sentences=sentences)
        send_response("According to Wikipedia:")
        send_response(results)
        return results
    except wikipedia.DisambiguationError as e:
        send_response("There are multiple entries for this topic. Please be more specific.")
    except wikipedia.PageError:
        send_response("I couldn't find any relevant page on Wikipedia.")
    except Exception as e:
        send_response("An error occurred while fetching data from Wikipedia.")
    return None