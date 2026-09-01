import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

from vad.vad_service import (
    VADService,
    load_config
)
from core.executor import Executor

import pandora_core
import speech.tts as tts


# -----------------------------------
# ROOT
# -----------------------------------

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0,
    str(ROOT / "python")
)

load_dotenv(ROOT / ".env.local")


# -----------------------------------
# COLORS
# -----------------------------------

class C:

    RESET="\033[0m"

    RED="\033[91m"
    GREEN="\033[92m"
    YELLOW="\033[93m"
    BLUE="\033[94m"
    CYAN="\033[96m"
    MAGENTA="\033[95m"


# -----------------------------------
# LOGGING
# -----------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)


# -----------------------------------
# STATUS
# -----------------------------------

def status(
    label,
    msg,
    color=C.BLUE
):

    print(
        f"{color}[{label}]"
        f"{C.RESET} "
        f"{msg}"
    )


# -----------------------------------
# RUST
# -----------------------------------

try:

    status(
        "RUST",
        "Loading engine...",
        C.YELLOW
    )

    pattern_count=(
        pandora_core.pattern_count()
    )

    status(
        "RUST",
        f"Patterns: {pattern_count}",
        C.GREEN
    )

except Exception as e:

    status(
        "ERROR",
        f"Rust init failed:\n{e}",
        C.RED
    )

    sys.exit()


# -----------------------------------
# EXECUTOR
# -----------------------------------

executor=Executor()


# -----------------------------------
# TRANSCRIPT CALLBACK
# -----------------------------------

def on_transcript(
    text:str
):

    try:

        print()

        status(
            "USER",
            text,
            C.BLUE
        )

        # --------------------------
        # Rust intent engine
        # --------------------------

        result=(
            pandora_core
            .process_stream(
                text
            )
        )

        try:

            result=json.loads(
                result
            )

        except:

            result={

                "intent":
                    "unknown",

                "raw":
                    result
            }


        status(
            "INTENT",
            json.dumps(
                result,
                indent=2
            ),
            C.MAGENTA
        )


        # --------------------------
        # execution
        # --------------------------

        response=(
            executor
            .execute(
                result
            )
        )


        if response:

            status(
                "PANDORA",
                response,
                C.GREEN
            )

            tts.speak(
                response
            )

        print()

    except Exception as e:

        status(
            "ERROR",
            str(e),
            C.RED
        )


# -----------------------------------
# ENTRY
# -----------------------------------

if __name__=="__main__":

    print(
r"""

██████╗  █████╗ ███╗   ██╗██████╗  ██████╗ ██████╗  █████╗
██╔══██╗██╔══██╗████╗  ██║██╔══██╗██╔═══██╗██╔══██╗██╔══██╗
██████╔╝███████║██╔██╗ ██║██║  ██║██║   ██║██████╔╝███████║
██╔═══╝ ██╔══██║██║╚██╗██║██║  ██║██║   ██║██╔══██╗██╔══██║
██║     ██║  ██║██║ ╚████║██████╔╝╚██████╔╝██║  ██║██║  ██║
╚═╝     ╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝

"""
    )

    status(
        "READY",
        "Pandora runtime online",
        C.GREEN
    )

    status(
        "MIC",
        "Listening...",
        C.CYAN
    )

    cfg = load_config()

    service = VADService(cfg)

    try:

        service.start()

    except KeyboardInterrupt:

        status(
            "SYSTEM",
            "Shutdown requested",
            C.RED
        )

        service.stop()