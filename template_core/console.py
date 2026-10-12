"""Console helpers for the command-line tools."""

import sys


def utf8_stdout() -> None:
    """Make stdout/stderr UTF-8 so model output and symbols print correctly on Windows consoles and pipes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
