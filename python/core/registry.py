import webbrowser
import subprocess
import datetime


class ActionRegistry:

    def __init__(self):

        self.routes = {

            # ---------------- Dialogue ----------------

            "wakeword": self.wakeword,
            "greeting_hello": self.greeting,
            "greeting_general": self.greeting,
            "presence_check": self.presence,
            "identity_query": self.identity,
            "wake_up": self.wake_up,
            "go_to_sleep": self.sleep,
            "shutdown": self.shutdown,
            "mute": self.mute,
            "unmute": self.unmute,
            "progress_summary": self.progress,


            # ---------------- Query ----------------

            "query_time": self.query_time,
            "query_date": self.query_date,
            "query_version": self.query_version,


            # ---------------- Search ----------------

            "search_google": self.google,
            "search_wikipedia": self.wikipedia,
            "weather_query": self.weather,
            "weather_query_default": self.weather,
            "news_headlines": self.news,


            # ---------------- Browser ----------------

            "browser_open_known": self.browser,
            "browser_open_url": self.browser,
            "browser_open_generic": self.browser,


            # ---------------- Apps ----------------

            "app_open": self.app_open,
            "app_close": self.app_close,


            # ---------------- Notes ----------------

            "note_create": self.note_create,
            "note_search": self.note_search,


            # ---------------- Tasks ----------------

            "task_add": self.task_add,
            "task_list": self.task_list,


            # ---------------- Memory ----------------

            "memory_recall_topics": self.memory,
            "memory_recall_conversations": self.memory,


            # ---------------- Fallback ----------------

            "unknown": self.unknown
        }


    def get(self, intent):

        return self.routes.get(
            intent,
            self.unknown
        )


    # ==========================================
    # Dialogue
    # ==========================================

    def wakeword(self,e):
        return "Yes?"

    def greeting(self,e):
        return "Hello"

    def presence(self,e):
        return "I am here"

    def identity(self,e):
        return "I am Pandora"

    def wake_up(self,e):
        return "I'm online"

    def sleep(self,e):
        return "Entering standby"

    def shutdown(self,e):
        return "Shutting down Pandora"


    def mute(self,e):
        return "Muted"


    def unmute(self,e):
        return "Speaking again"


    def progress(self,e):
        return "Generating summary"


    # ==========================================
    # Query
    # ==========================================

    def query_time(self,e):

        return datetime.datetime.now().strftime(
            "Current time is %I:%M %p"
        )


    def query_date(self,e):

        return datetime.datetime.now().strftime(
            "Today is %B %d %Y"
        )


    def query_version(self,e):

        return "Pandora version 1.0"


    # ==========================================
    # Search
    # ==========================================

    def google(self,e):

        q=e.get("query","")

        webbrowser.open(
            f"https://www.google.com/search?q={q}"
        )

        return f"Searching {q}"


    def wikipedia(self,e):

        topic=e.get("topic","")

        webbrowser.open(
            f"https://en.wikipedia.org/wiki/{topic}"
        )

        return f"Searching Wikipedia for {topic}"


    def weather(self,e):

        location=e.get(
            "location",
            "current location"
        )

        return (
            f"Fetching weather for {location}"
        )


    def news(self,e):

        return "Fetching latest news"


    # ==========================================
    # Browser
    # ==========================================

    def browser(self,e):

        site=e.get("site")

        url=e.get("url")

        if url:

            webbrowser.open(url)

            return f"Opening {url}"

        if site:

            webbrowser.open(
                f"https://{site}.com"
            )

            return f"Opening {site}"

        return "Opening browser"


    # ==========================================
    # Apps
    # ==========================================

    def app_open(self,e):

        app=e.get("app","")

        subprocess.Popen(app)

        return f"Opening {app}"


    def app_close(self,e):

        app=e.get("app","")

        subprocess.run(
            f"taskkill /F /IM {app}.exe",
            shell=True
        )

        return f"Closing {app}"


    # ==========================================
    # Notes
    # ==========================================

    def note_create(self,e):

        return "Ready for note"


    def note_search(self,e):

        q=e.get("query","")

        return f"Searching notes: {q}"


    # ==========================================
    # Tasks
    # ==========================================

    def task_add(self,e):

        desc=e.get(
            "description",
            ""
        )

        return f"Task added: {desc}"


    def task_list(self,e):

        return "Listing tasks"


    # ==========================================
    # Memory
    # ==========================================

    def memory(self,e):

        return "Searching memory"


    # ==========================================

    def unknown(self,e):

        return (
            "I couldn't understand that."
        )