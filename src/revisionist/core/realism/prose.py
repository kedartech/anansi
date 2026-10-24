"""The prose post-check: a written page may state no figure its page does not.

The prose model sees only the ``DocumentView``, so any number in its prose that
the page lacks was invented. ``invented_numbers`` finds them; the engine drops
prose that has any, and the document keeps its body lines alone.

What counts as on the page [proposed]: the body lines (values, labels, units),
the title, and the day, month and year of the issue and arrival dates, since a
period letter dates itself. Numbers written as words are not checked.
"""

import re
from collections.abc import Iterable

from revisionist.core.realism.document import DocumentView

_NUMBER = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Every number in ``text``, thousands separators dropped and a trailing ``.0`` kept."""
    return [m.group().replace(",", "") for m in _NUMBER.finditer(text)]


def _canon(number: str) -> str:
    whole, _, frac = number.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def page_numbers(page: DocumentView) -> frozenset[str]:
    """The numbers a page states, in canonical form."""
    found: list[str] = []
    for line in page.body:
        found += numbers_in(f"{line.label} {line.value if line.value is not None else ''}")
        found += numbers_in(line.unit)
    found += numbers_in(page.title)
    for day in (page.issued, page.arrives):
        found += [str(day.year), str(day.month), str(day.day)]
    return frozenset(_canon(n) for n in found)


def invented_numbers(prose: str, pages: DocumentView | Iterable[DocumentView]) -> tuple[str, ...]:
    """Numbers in ``prose`` that none of ``pages`` states, in order of first use."""
    allowed: set[str] = set()
    for page in [pages] if isinstance(pages, DocumentView) else pages:
        allowed |= page_numbers(page)
    out: list[str] = []
    for n in numbers_in(prose):
        if _canon(n) not in allowed and n not in out:
            out.append(n)
    return tuple(out)


__all__ = ["invented_numbers", "numbers_in", "page_numbers"]
