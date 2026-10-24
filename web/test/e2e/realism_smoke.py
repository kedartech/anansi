"""Browser smoke run of the realism room against a live `revisionist web serve`.

Signs up a fresh account, joins a realism game with an invite code, and checks the
in-tray folders, an open dossier and its flag, the filters, the desk, a directive
read by the aide (signed when it reads as an order, revised when it cannot be sent),
an order signed on a standard form, the advance and the situation board, screenshotting each tab at
1440x900. Fails on any console error, page error, failed request or a request to
another origin, or any named check that does not hold; runs axe-core when it is
mounted at /axe/axe.min.js.

    python web/test/e2e/realism_smoke.py BASE_URL INVITE_CODE OUT_DIR
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
        page.get_by_label("Invite code").fill(CODE)
        page.get_by_role("button", name="Join").click()

        page.get_by_role("tab", name="In-tray").wait_for(timeout=30_000)
        page.wait_for_timeout(300)
        shoot(page, "00-opening")
        if page.locator(".rl-folder").count() == 0:
            # A new game's first papers arrive on the first wake; prose is written live.
            page.get_by_role("button", name="Advance to next wake").click()
            page.get_by_role("status").filter(has_text="calendar moves").wait_for(timeout=WAKE_MS)
            page.wait_for_timeout(300)
        tray = page.locator(".rl-folder")
        n = tray.count()
        check(f"in-tray renders {n} folders", n > 0)
        shoot(page, "01-in-tray")
        axe(page, "in-tray")

        unread = page.locator(".rl-folder.unread").count()
        page.locator(".rl-folder.unread .rl-cover").first.click()
        page.locator(".rl-dossier .rl-page").wait_for()
        page.wait_for_timeout(300)
        prose = page.locator(".rl-prose")
        text = prose.inner_text() if prose.count() else ""
        check("an opened paper has written text", len(text.strip()) > 40, f"{len(text)} chars")
        report["sample_prose"] = text[:600]
        check(
            "no paper reads 'Not stated'", "Not stated" not in page.locator(".rl-page").inner_text()
        )
        shoot(page, "02-dossier")
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
        shoot(page, "03-in-tray-flagged")
        page.get_by_role("tab", name="Unread").click()
        page.wait_for_timeout(200)
        check("Unread filter shows the rest", page.locator(".rl-folder").count() == now_unread)
        page.get_by_role("tab", name="All").click()

        page.get_by_role("tab", name="Desk").click()
        instruments = page.locator(".rl-instrument")
        check("desk lists instruments", instruments.count() > 0)
        snake = [t for t in instruments.locator("strong").all_inner_texts() if "_" in t]
        check("instrument names are in words", not snake, ", ".join(snake))
        shoot(page, "04-desk")
        axe(page, "desk")

        page.get_by_role("tab", name="Orders").click()
        before = page.locator(".rl-order").count()
        page.get_by_role("tab", name="Desk").click()
        words = "Send word that our escorts will see the convoys as far as Iceland."
        directive = page.locator("form.rl-directive")
        check("the desk offers a directive", directive.count() == 1)
        directive.get_by_label("Your instruction").fill(words)
        directive.get_by_role("button", name="Prepare the order").click()
        card = page.locator(".rl-proposal")
        card.wait_for(timeout=WAKE_MS)
        page.wait_for_timeout(300)
        report["directive_reading"] = card.inner_text()
        shoot(page, "05-directive-reading")
        axe(page, "directive")
        check("the reading quotes the instruction", words in card.inner_text())
        check("the reading is in words", "_" not in card.inner_text(), card.inner_text())
        sign = card.get_by_role("button", name="Sign and send")
        if sign.count():
            sign.click()
            page.get_by_role("status").filter(has_text="Sent to").wait_for()
            report["directive_reply"] = page.get_by_role("status").inner_text()
            before += 1
        else:
            card.get_by_role("button", name="Revise").click()
            kept = page.locator("form.rl-directive textarea").input_value()
            check("Revise keeps the instruction", kept == words, kept)
        page.wait_for_timeout(300)
        page.get_by_role("tab", name="Orders").click()
        filed = page.locator(".rl-order").count()
        check("a directive files only when signed", filed == before, f"{before} -> {filed}")
        page.get_by_role("tab", name="Desk").click()
        first = page.locator(".rl-instrument").first
        first.get_by_role("button", name="Draft").click()
        form = first.locator("form.rl-form")
        form.wait_for()
        fill(form)
        shoot(page, "06-desk-draft")
        form.get_by_role("button", name="Sign and send").click()
        page.get_by_role("status").filter(has_text="Sent to").wait_for()
        report["sent_reply"] = page.get_by_role("status").inner_text()
        page.wait_for_timeout(300)
        page.get_by_role("tab", name="Orders").click()
        after = page.locator(".rl-order").count()
        check("signing an order adds a tracker entry", after == before + 1, f"{before} -> {after}")
        shoot(page, "07-orders")
        axe(page, "orders")

        pace = page.locator(".rl-pace").first.inner_text()
        t0 = time.time()
        page.get_by_role("button", name="Advance to next wake").click()
        page.get_by_role("status").filter(has_text="calendar moves").wait_for(timeout=WAKE_MS)
        report["advance_seconds"] = round(time.time() - t0, 1)
        page.wait_for_timeout(300)
        moved = page.locator(".rl-pace").first.inner_text()
        check("advance moves the world date", moved != pace, f"{pace!r} -> {moved!r}")
        page.get_by_role("tab", name="In-tray").click()
        page.wait_for_timeout(300)
        shoot(page, "08-in-tray-after-advance")
        titles = page.locator(".rl-folder .rl-doc-title").all_inner_texts()
        report["titles_after_advance"] = titles
        check("an order reply arrives as a paper", any(t.startswith("On your") for t in titles))
        check(
            "no title shows an ISO date",
            not any(c.isdigit() and "-" in t for t in titles for c in t[-3:]),
        )
        reply = page.locator(".rl-folder", has_text="On your").first
        if reply.count():
            reply.locator(".rl-cover").click()
            page.locator(".rl-dossier .rl-page").wait_for()
            page.wait_for_timeout(300)
            report["reply_page"] = page.locator(".rl-page").inner_text()[:800]
            shoot(page, "09-reply-letter")
            axe(page, "reply")
        report["pace_after_advance"] = moved
        check("pace line has no engine words", "horizon" not in moved, moved)

        page.get_by_role("tab", name="Situation").click()
        page.wait_for_timeout(300)
        cards = page.locator(".rl-card")
        check("the situation board shows figures", cards.count() > 0)
        report["situation"] = page.locator(".rl-board").inner_text()[:1200]
        shoot(page, "10-situation")
        axe(page, "situation")

        body = page.locator("body").inner_text()
        for word in ("controller", "human", "truth", " AI ", "%"):
            if word in body:
                problems.append(f"rule: page text contains {word!r}")
        browser.close()
    report["checks"] = checks
    report["problems"] = problems
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
