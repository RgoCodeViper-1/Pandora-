"""
utils/decorators.py
====================
Shared decorators for handler functions.
"""

import functools
import logging
import time

logger = logging.getLogger("pandora.handlers")


def safe_execute(default_message: str = "Something went wrong while handling that request."):
    """
    Catch and log any exception raised by a handler function, returning a
    safe fallback string instead of letting it propagate up to the executor
    and surface as a raw traceback.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                logger.warning("%s raised: %s", func.__name__, exc)
                return default_message
        return wrapper
    return decorator


def timed(func):
    """Log execution time of a handler call — useful for spotting slow
    network-bound handlers (weather, news, search)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        result = func(*args, **kwargs)
        elapsed = time.monotonic() - start
        logger.debug("%s took %.3fs", func.__name__, elapsed)
        return result
    return wrapper
