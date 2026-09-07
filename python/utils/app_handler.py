"""
utils/app_handler.py
=====================
Application launch / close execution worker.

Invoked by core/registry.py for the "app_open" / "app_close" intents.
Entities come straight from the Rust engine's `app` capture group.
"""

import os
import platform

from utils.decorators import safe_execute
from utils.util_response import send_response

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@safe_execute("I couldn't open that application.")
def open_app(e: dict) -> str:
    """Launch an application by name, cross-platform. `e` is the entities
    dict from the matched intent (expects an 'app' slot)."""
    app_name = (e.get("app") or "").strip()
    if not app_name:
        return send_response("Which application would you like me to open?")

    system = platform.system()
    if system == "Windows":
        os.startfile(app_name)  # noqa: S606 — user-issued voice command
    elif system == "Darwin":
        os.system(f"open -a '{app_name}'")
    else:
        os.system(f"{app_name} &")

    return send_response(f"Opening {app_name}.")


@safe_execute("I couldn't close that application.")
def close_app(e: dict) -> str:
    """Terminate the first running process whose name matches the 'app' slot."""
    app_name = (e.get("app") or "").strip()
    if not app_name:
        return send_response("Which application would you like me to close?")

    if not PSUTIL_AVAILABLE:
        return send_response("Process management isn't available right now.")

    closed = False
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if app_name.lower() in (proc.info["name"] or "").lower():
                proc.terminate()
                closed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if closed:
        return send_response(f"Closed {app_name}.")
    return send_response(f"I couldn't find a running process matching {app_name}.")
