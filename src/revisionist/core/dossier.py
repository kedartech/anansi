"""The opening dossier (playtest #28): one seat's whole starting position, built in code.

Built from the seat's ``View`` only, so it cannot name anything the seat may not
see, and it costs no model call. Unit U6 fills it in; U7 sends it at ``/begin``.
"""

from revisionist.core.state import View


def dossier(view: View) -> list[str]:
    """Plain lines. A line ending in ``:`` is a section header; the rest are items."""
    return []


__all__ = ["dossier"]
