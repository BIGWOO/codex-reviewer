"""Small platform adaptations without changing the caller's global settings."""

import os
import sys


def restrict_file_permissions(descriptor: int) -> None:
    # Windows uses inherited ACLs; chmod cannot implement POSIX owner-only access.
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)


def configure_console() -> None:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
