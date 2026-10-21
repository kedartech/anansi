"""Discord markup for the plain lines the engine returns.

Engine text stays plain; the GUI will format the same lines its own way.
"""

import re
from collections.abc import Sequence

from revisionist.engine.session_zero import SUGGESTED

HISTORY_LABELS = ("As recorded:", "The goal in history:", SUGGESTED, "Where the record is thin:")
NAME_END = (" (", " [", ": ")
OFFER = re.compile(r"([HCO]\d+)\. ")


def bullets(lines: Sequence[str]) -> str:
    """Each line as a bullet; a header (ending in ':') is bold and not bulleted."""
    return "\n".join(f"**{line}**" if line.endswith(":") else f"- {line}" for line in lines)


def named(line: str) -> str:
    """Bold the name, the text before the first tag or colon."""
    ends = [i for i in (line.find(sep) for sep in NAME_END) if i >= 0]
    if not ends:
        return line
    end = min(ends)
    return f"**{line[:end]}**{line[end:]}"


def goal_offer(line: str) -> str:
    """A goal offer as a bullet with its label bold: ``H1. text`` becomes ``- **H1** text``."""
    match = OFFER.match(line)
    if match is None:
        return line
    return f"- **{match.group(1)}** {line[match.end() :]}"


def history(text: str) -> str:
    """Bold the label that opens each paragraph of the tick-0 brief."""
    paragraphs = []
    for para in text.split("\n\n"):
        for label in HISTORY_LABELS:
            if para.startswith(label):
                para = f"**{label}**{para[len(label) :]}"
                break
        paragraphs.append(para)
    return "\n\n".join(paragraphs)


def dashboard(lines: Sequence[str]) -> str:
    """The seat's first line in bold, the rest as bullets."""
    if not lines:
        return ""
    head = f"**{lines[0]}**"
    rest = bullets(lines[1:])
    return f"{head}\n{rest}" if rest else head
