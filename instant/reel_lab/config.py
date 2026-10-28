"""Read explicitly selected local chat settings without shell evaluation."""
from __future__ import annotations

import os
import re
from pathlib import Path


_CHAT_KEYS = {"INSTANT_CHAT_BASE_URL", "INSTANT_CHAT_MODEL", "INSTANT_CHAT_API_KEY"}
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INVALID = "Local chat configuration must contain simple KEY=value lines with optional whole-value quotes."


def load_chat_environment(path):
    """Load allowlisted settings, preserving every existing environment value.

    Missing files are harmless. The entire file is validated before applying
    any settings. Values are literal: no interpolation, escaping, commands or
    inline-comment processing occurs. Return only the number of settings added.
    """
    try:
        contents = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeError):
        raise ValueError("Local chat configuration could not be read.") from None
    pending = {}
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(_INVALID)
        key, value = (part.strip() for part in line.split("=", 1))
        if not _KEY.fullmatch(key) or "\x00" in value:
            raise ValueError(_INVALID)
        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote or quote in value[1:-1]:
                raise ValueError(_INVALID)
            value = value[1:-1]
        elif "'" in value or '"' in value:
            raise ValueError(_INVALID)
        if key in _CHAT_KEYS:
            if key in pending:
                raise ValueError(_INVALID)
            pending[key] = value
    added = 0
    for key, value in pending.items():
        # An explicitly empty process setting also takes precedence.
        if key not in os.environ:
            os.environ[key] = value
            added += 1
    return added
