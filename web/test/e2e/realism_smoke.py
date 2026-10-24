"""Browser smoke run of the realism room against a live `revisionist web serve`.

Signs up a fresh account, joins a realism game with an invite code, and checks the
in-tray, the desk, a filed order and the advance, screenshotting each tab at
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
        if page.locator(".rl-doc").count() == 0:
            # A new game's first documents arrive on the first tick.
            page.get_by_role("button", name="Advance to next wake").click()
            page.get_by_role("status").filter(has_text="woke on").wait_for(timeout=30_000)
            page.wait_for_timeout(300)
        tray = page.locator(".rl-doc")
        n = tray.count()
        check(f"in-tray renders {n} documents", n > 0)
        shoot(page, "01-in-tray")
        axe(page, "in-tray")

        unread = page.locator(".rl-doc.unread").count()
        page.locator(".rl-doc.unread .rl-doc-open").first.click()
        page.locator(".rl-page").wait_for()
        page.wait_for_timeout(300)
        now_unread = page.locator(".rl-doc.unread").count()
        check(
            "opening a document marks it read",
            unread > 0 and now_unread == unread - 1,
            f"unread {unread} -> {now_unread}",
        )
        shoot(page, "02-in-tray-reading")

        page.get_by_role("tab", name="Desk").click()
        forms = page.locator("form.rl-form")
        check("desk lists levers", forms.count() > 0)
        shoot(page, "03-desk")
        axe(page, "desk")

        page.get_by_role("tab", name="Orders").click()
        before = page.locator(".rl-order").count()
        page.get_by_role("tab", name="Desk").click()
        form = page.locator("form.rl-form").first
        fill(form)
        form.get_by_role("button", name="File order").click()
        page.get_by_role("status").filter(has_text="Filed").wait_for()
        page.wait_for_timeout(300)
        page.get_by_role("tab", name="Orders").click()
        after = page.locator(".rl-order").count()
        check("filing an order adds a tracker entry", after == before + 1, f"{before} -> {after}")
        shoot(page, "04-orders")
        axe(page, "orders")

        pace = page.locator(".rl-pace").first.inner_text()
        page.get_by_role("button", name="Advance to next wake").click()
        page.get_by_role("status").filter(has_text="woke on").wait_for(timeout=30_000)
        page.wait_for_timeout(300)
        moved = page.locator(".rl-pace").first.inner_text()
        check("advance moves the world date", moved != pace, f"{pace!r} -> {moved!r}")

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
