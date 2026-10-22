"""Discord markup for the plain lines the engine returns.

Engine text stays plain; the GUI will format the same lines its own way.
"""

import re
from collections.abc import Sequence

from revisionist.engine.session_zero import SUGGESTED

HISTORY_LABELS = ("As recorded:", "The goal in history:", SUGGESTED, "Where the record is thin:")
NAME_END = (" (", " [", ": ")
OFFER = re.compile(r"([HCO]\d+)\. ")
CARD_START = re.compile(r"(\d+)\. (.+), (\d{1,2} \w+ \d{4})\. (Focus: .*)")
CARD_ITEM = re.compile(r"   (\d+)\) (.*)")


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


def card(lines: Sequence[str]) -> str:
    """A setting card's ``card_lines``: each start point's label bold, its history numbered."""
    out = []
    for line in lines:
        if start := CARD_START.match(line):
            n, label, day, focus = start.groups()
            out.append(f"{n}. **{label}**, {day}. {focus}")
        elif item := CARD_ITEM.match(line):
            out.append(f"   {item[1]}. {item[2]}")
        else:
            out.append(line)
    return "\n".join(out)


def dashboard(lines: Sequence[str]) -> str:
    """The seat's first line in bold, the rest as bullets."""
    if not lines:
        return ""
    head = f"**{lines[0]}**"
    rest = bullets(lines[1:])
    return f"{head}\n{rest}" if rest else head
