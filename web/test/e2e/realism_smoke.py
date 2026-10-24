"""Browser smoke run of the realism room against a live `revisionist web serve`.

Signs up a fresh account and either joins a realism game with an invite code or, with
the code `new`, starts the ready-made game as its host and plays Franklin D. Roosevelt.
Checks the one-surface room: the header names the player, the game menu, the busy
state while a wake runs, in-tray folders and an opened paper read beside the desk, the
filters, recipients with their titles, a directive read by the aide (signed when it
reads as an order, revised when it cannot be sent), an order signed on a standard form,
the pending orders sidebar, the aide brief and consult, the situation board, the map,
that no empty-state filler shows, and (as host) ending the game. Screenshots at
1440x900. Fails on any console error, page error, failed request or a request to
another origin, or any named check that does not hold; runs axe-core when it is
mounted at /axe/axe.min.js.

    python web/test/e2e/realism_smoke.py BASE_URL INVITE_CODE|new OUT_DIR

For a run with no model calls, serve with web/test/e2e/serve_canned.py.
"""

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, sync_playwright

BASE, CODE, OUT = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
SIZE = (1440, 900)
AXE = Path("/axe/axe.min.js")
WAKE_MS = 240_000  # a wake makes one live prose call per author
problems: list[str] = []
checks: dict[str, bool] = {}
origin = urlparse(BASE).netloc


def watch(page: Page) -> None:
    page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: problems.append(f"failed: {r.url} {r.failure}"))

    def request(r: object) -> None:
        url = r.url  # type: ignore[attr-defined]
        if not url.startswith("data:") and urlparse(url).netloc != origin:
            problems.append(f"outside request: {url}")

    page.on("request", request)


def axe(page: Page, name: str) -> list[dict]:
    if not AXE.is_file():
        return []
    page.evaluate(AXE.read_text())  # CDP evaluate, so the page's own CSP still holds
    found = page.evaluate(
        "async () => (await axe.run(document, {runOnly: ['wcag2a','wcag2aa']})).violations"
        ".map(v => ({id: v.id, impact: v.impact, n: v.nodes.length,"
        " first: v.nodes[0] && v.nodes[0].target.join(' ')}))"
    )
    for v in found:
        problems.append(f"axe {name}: {v['id']} ({v['impact']}) x{v['n']} at {v['first']}")
    return found


def shoot(page: Page, name: str) -> None:
    page.wait_for_timeout(150)
    page.screenshot(path=str(OUT / f"{name}-{SIZE[0]}x{SIZE[1]}.png"))


def check(name: str, ok: bool, detail: str = "") -> None:
    checks[name] = ok
    if not ok:
        problems.append(f"check failed: {name} {detail}".strip())


FILLER = (
    "No figure given",
    "has not reached your desk",
    "nothing firm",
    "no figure to give",
    "Not stated",
)
"""Empty-state filler that must not reach the screen."""

SLOW_ADVANCE = (
    "() => { const f = window.fetch; window.fetch = (u, i) => String(u).includes('/advance')"
    " ? new Promise((r) => setTimeout(r, 2000)).then(() => f(u, i)) : f(u, i); }"
)
"""Holds the advance call for two seconds so the busy state can be seen and shot."""


def no_filler(page: Page, where: str) -> None:
    text = page.locator("main").inner_text()
    found = [f for f in FILLER if f.lower() in text.lower()]
    check(f"no empty-state filler on {where}", not found, ", ".join(found))


def advance(page: Page, name: str) -> None:
    """Advance with the call held, and check the busy state while it runs."""
    page.evaluate(SLOW_ADVANCE)
    page.get_by_role("button", name="Advance to next wake").click()
    busy = page.locator(".rl-status.busy")
    busy.wait_for(timeout=5_000)
    check(f"{name}: busy line says the wake is running", "wake is running" in busy.inner_text())
    check(
        f"{name}: busy line is announced",
        busy.get_attribute("role") == "status" and busy.get_attribute("aria-live") == "polite",
    )
    check(
        f"{name}: advance is held while busy",
        page.get_by_role("button", name="Advance to next wake").is_disabled(),
    )
    shoot(page, f"{name}-busy")
    page.locator(".rl-status.said").filter(has_text="calendar moves").wait_for(timeout=WAKE_MS)
    page.wait_for_timeout(300)


def fill(form: Locator) -> None:
    """Answer every field of a lever form with a value its input accepts."""
    for field in form.locator(".rl-field").all():
        number, select = field.locator("input[type=number]"), field.locator("select")
        if number.count():
            number.fill(number.get_attribute("min") or "1")
        elif select.count():
            select.select_option(index=1)
        else:
            field.locator("textarea").fill("Smoke test order")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]}, bypass_csp=False)
        watch(page)
        page.goto(BASE + "/")
        page.get_by_role("link", name="Create an account").click()
        page.get_by_role("heading", name="Create an account").wait_for()
        page.get_by_label("Handle").fill(f"rsmoke{int(time.time()) % 100000}")
        page.get_by_label("Password").fill("a long enough passphrase")
        page.get_by_role("button", name="Create account").click()
        page.get_by_role("heading", name="Your recovery codes").wait_for()
        page.get_by_label("I've kept these somewhere safe").check()
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("heading", name="Join a game").wait_for()
        host = CODE == "new"
        if host:
            page.get_by_role("link", name="Start a new game").click()
            page.get_by_role("button", name="Start Autumn 1941").click()
            page.get_by_role("heading", name="Choose who you will play").wait_for(timeout=30_000)
            page.get_by_role("button", name="Play Franklin D. Roosevelt").click()
            page.get_by_role("heading", name="You are Franklin D. Roosevelt").wait_for(
                timeout=60_000
            )
            page.get_by_role("button", name="Begin the game").click()
        else:
            page.get_by_label("Invite code").fill(CODE)
            page.get_by_role("button", name="Join").click()

        page.get_by_role("tab", name="In-tray").wait_for(timeout=60_000)
        page.wait_for_timeout(300)
        shoot(page, "00-opening")
        who = page.locator(".frame-head .identity")
        report["header_identity"] = who.inner_text() if who.count() else ""
        check(
            "the header names who the player plays",
            who.count() == 1
            and "playing as" in who.inner_text().lower()
            and len(who.inner_text()) > 20,
            str(report["header_identity"]),
        )
        page.get_by_role("button", name="Game").click()
        menu = page.locator("dialog.rl-menu")
        menu.wait_for()
        report["game_menu"] = menu.inner_text()
        check("the game menu opens from the header", menu.is_visible())
        if host:
            check(
                "the host's menu offers End game",
                menu.get_by_role("button", name="End game…").count() == 1,
            )
            check(
                "the host's menu offers Pause",
                menu.get_by_role("button", name="Pause").count() == 1,
            )
        shoot(page, "01-game-menu")
        menu.get_by_role("button", name="Close").click()

        if page.locator(".rl-folder").count() == 0:
            # A new game's first papers arrive on the first wake.
            advance(page, "02-first-wake")
        tray = page.locator(".rl-folder")
        n = tray.count()
        check(f"in-tray renders {n} folders", n > 0)
        check("the desk shows beside the in-tray", page.locator(".rl-desk").is_visible())
        check("pending orders show beside the desk", page.locator(".rl-pending").is_visible())
        check("the aide shows beside the desk", page.locator(".rl-aide").is_visible())
        shoot(page, "03-room")
        axe(page, "room")

        unread = page.locator(".rl-folder.unread").count()
        page.locator(".rl-folder.unread .rl-cover").first.click()
        page.locator(".rl-dossier .rl-page").wait_for()
        page.wait_for_timeout(300)
        prose = page.locator(".rl-prose")
        text = prose.inner_text() if prose.count() else ""
        check("an opened paper reads beside the desk", page.locator(".rl-desk").is_visible())
        report["sample_prose"] = text[:600]
        no_filler(page, "an opened paper")
        shoot(page, "04-paper-beside-desk")
        axe(page, "dossier")

        page.get_by_role("button", name="Flag", exact=True).click()
        page.get_by_role("button", name="Unflag").wait_for()
        page.get_by_role("button", name="Close folder").click()
        page.locator(".rl-tray").wait_for()
        page.wait_for_timeout(300)
        now_unread = page.locator(".rl-folder.unread").count()
        check(
            "opening a folder marks it read",
            unread > 0 and now_unread == unread - 1,
            f"unread {unread} -> {now_unread}",
        )
        check("flagging marks the folder", page.locator(".rl-folder.flagged").count() == 1)
        page.get_by_role("tab", name="Flagged").click()
        page.wait_for_timeout(200)
        check("Flagged filter shows the one folder", page.locator(".rl-folder").count() == 1)
        page.get_by_role("tab", name="Unread").click()
        page.wait_for_timeout(200)
        check("Unread filter shows the rest", page.locator(".rl-folder").count() == now_unread)
        page.get_by_role("tab", name="All").click()

        instruments = page.locator(".rl-instrument")
        check("desk lists instruments", instruments.count() > 0)
        snake = [t for t in instruments.locator("strong").all_inner_texts() if "_" in t]
        check("instrument names are in words", not snake, ", ".join(snake))
        people = page.locator(".rl-recipient")
        titles = people.locator(".rl-person-title").all_inner_texts()
        report["recipients"] = people.all_inner_texts()
        check(
            "each recipient shows a title",
            people.count() > 0 and len(titles) == people.count() and all(titles),
            str(report["recipients"]),
        )
        page.locator(".rl-desk-col .rl-col-body").evaluate("el => el.scrollTo(0, 0)")
        shoot(page, "05-desk-recipients")

        before = page.locator(".rl-pending .rl-order").count()
        words = "Send word that our escorts will see the convoys as far as Iceland."
        directive = page.locator("form.rl-directive")
        check("the desk offers a directive", directive.count() == 1)
        directive.get_by_label("Your instruction").fill(words)
        directive.get_by_role("button", name="Prepare the order").click()
        check(
            "preparing the order shows the aide at work",
            page.locator(".rl-status.busy").filter(has_text="aide").count() == 1
            or page.locator(".rl-proposal").count() == 1,
        )
        card = page.locator(".rl-proposal")
        card.wait_for(timeout=WAKE_MS)
        page.wait_for_timeout(300)
        report["directive_reading"] = card.inner_text()
        shoot(page, "06-directive-reading")
        axe(page, "directive")
        check("the reading quotes the instruction", words in card.inner_text())
        check("the reading is in words", "_" not in card.inner_text(), card.inner_text())
        sign = card.get_by_role("button", name="Sign and send")
        if sign.count():
            sign.click()
            page.locator(".rl-status.said").filter(has_text="Sent to").wait_for()
            report["directive_reply"] = page.locator(".rl-status").inner_text()
            before += 1
        else:
            card.get_by_role("button", name="Revise").click()
            kept = page.locator("form.rl-directive textarea").input_value()
            check("Revise keeps the instruction", kept == words, kept)
        page.wait_for_timeout(300)
        filed = page.locator(".rl-pending .rl-order").count()
        check("a directive files only when signed", filed == before, f"{before} -> {filed}")
        first = page.locator(".rl-instrument").first
        first.get_by_role("button", name="Draft").click()
        form = first.locator("form.rl-form")
        form.wait_for()
        fill(form)
        form.get_by_role("button", name="Sign and send").click()
        page.locator(".rl-status.said").filter(has_text="Sent to").wait_for()
        report["sent_reply"] = page.locator(".rl-status").inner_text()
        page.wait_for_timeout(300)
        after = page.locator(".rl-pending .rl-order").count()
        check("signing an order adds a pending order", after == before + 1, f"{before} -> {after}")
        shoot(page, "07-pending-orders")
        axe(page, "orders")

        page.get_by_role("button", name="Brief me").click()
        page.locator(".rl-brief").wait_for(timeout=WAKE_MS)
        report["aide_brief"] = page.locator(".rl-brief").inner_text()[:600]
        check("the aide briefs on request", len(page.locator(".rl-brief").inner_text()) > 20)
        aside = page.locator(".rl-aide")
        aside.get_by_label("Your question").fill("Should we wait for the Senate before we move?")
        aside.get_by_role("button", name="Ask them").click()
        page.locator(".rl-answer").first.wait_for(timeout=WAKE_MS)
        report["consult"] = page.locator(".rl-answer").first.inner_text()
        check("consult shows the answer by the aide", page.locator(".rl-answer").count() >= 1)
        shoot(page, "08-aide-and-consult")
        axe(page, "aide")

        pace = page.locator(".rl-pace").first.inner_text()
        t0 = time.time()
        advance(page, "09-advance")
        report["advance_seconds"] = round(time.time() - t0, 1)
        moved = page.locator(".rl-pace").first.inner_text()
        check("advance moves the world date", moved != pace, f"{pace!r} -> {moved!r}")
        titles = page.locator(".rl-folder .rl-doc-title").all_inner_texts()
        report["titles_after_advance"] = titles
        check("an order reply arrives as a paper", any(t.startswith("On your") for t in titles))
        check(
            "no title shows an ISO date",
            not any(c.isdigit() and "-" in t for t in titles for c in t[-3:]),
        )
        shoot(page, "10-room-after-advance")
        reply = page.locator(".rl-folder", has_text="On your").first
        if reply.count():
            reply.locator(".rl-cover").click()
            page.locator(".rl-dossier .rl-page").wait_for()
            page.wait_for_timeout(300)
            report["reply_page"] = page.locator(".rl-page").inner_text()[:800]
            written = page.locator(".rl-dossier .rl-prose")
            check(
                "a reply has written text",
                written.count() == 1 and len(written.inner_text().strip()) > 40,
            )
            no_filler(page, "a reply")
            shoot(page, "11-reply-letter")
            axe(page, "reply")
            page.get_by_role("button", name="Close folder").click()
        report["pace_after_advance"] = moved
        check("pace line has no engine words", "horizon" not in moved, moved)

        page.get_by_role("tab", name="Situation").click()
        page.wait_for_timeout(300)
        cards = page.locator(".rl-card")
        check("the situation board shows figures", cards.count() > 0)
        report["situation"] = page.locator(".rl-board").inner_text()[:1200]
        no_filler(page, "the situation board")
        shoot(page, "12-situation")
        axe(page, "situation")

        page.get_by_role("tab", name="Map").click()
        page.locator(".rl-map").wait_for()
        page.wait_for_timeout(800)
        marks = page.locator(".rl-map-svg .rl-mk")
        report["map_marks"] = marks.count()
        check("the map opens and shows marks", marks.count() > 0, f"{marks.count()} marks")
        check("the map draws coastlines", page.locator(".rl-map-land").count() == 1)
        shoot(page, "13-map")
        page.get_by_role("button", name="Widen").click()
        page.wait_for_timeout(500)
        shoot(page, "14-map-wide")
        axe(page, "map")
        page.get_by_role("button", name="Narrow").click()

        body = page.locator("body").inner_text()
        for word in ("controller", "human", "truth", " AI ", "%"):
            if word in body:
                problems.append(f"rule: page text contains {word!r}")

        if host:
            page.get_by_role("button", name="Game").click()
            menu = page.locator("dialog.rl-menu")
            menu.get_by_role("button", name="End game…").click()
            confirm = menu.locator(".rl-confirm")
            check(
                "ending asks first", confirm.is_visible() and "for everyone" in confirm.inner_text()
            )
            shoot(page, "15-end-confirm")
            menu.get_by_role("button", name="End the game").click()
            page.get_by_role("heading", name="Start a game").wait_for(timeout=30_000)
            check("after ending, the player can start a new game", True)
            shoot(page, "16-after-end")
        browser.close()
    report["checks"] = checks
    report["problems"] = problems
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
