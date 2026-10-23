"""Browser smoke run against a live `revisionist web serve` (real engine data).

Signs up, joins with an invite code, and screenshots every reachable screen at
three desktop sizes. Fails on any console error, page error, failed request or a
request to another origin; runs axe-core when it is mounted at /axe/axe.min.js.

    python web/test/e2e/smoke.py BASE_URL INVITE_CODE OUT_DIR
"""

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

BASE, CODE, OUT = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
SIZES = [(1440, 900), (1280, 800), (1920, 1080)]
AXE = Path("/axe/axe.min.js")
problems: list[str] = []
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
    for w, h in SIZES:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(150)
        page.screenshot(path=str(OUT / f"{name}-{w}x{h}.png"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, bypass_csp=False)
        watch(page)
        page.goto(BASE + "/")
        page.get_by_role("heading", name="Sign in").wait_for()
        shoot(page, "01-signin")
        axe(page, "signin")

        page.get_by_role("link", name="Create an account").click()
        page.get_by_role("heading", name="Create an account").wait_for()
        page.get_by_label("Handle").fill(f"smoke{int(time.time()) % 100000}")
        page.get_by_label("Password").fill("a long enough passphrase")
        shoot(page, "02-signup")
        axe(page, "signup")
        page.get_by_role("button", name="Create account").click()
        page.get_by_role("heading", name="Your recovery codes").wait_for()
        report["codes"] = page.locator("ol.codes li").count()
        shoot(page, "03-recovery-codes")
        axe(page, "codes")
        page.get_by_label("I've kept these somewhere safe").check()
        page.get_by_role("button", name="Continue").click()

        page.get_by_role("heading", name="Join a game").wait_for()
        shoot(page, "04-join")
        axe(page, "join")
        page.get_by_label("Invite code").fill(CODE)
        page.get_by_role("button", name="Join").click()

        page.locator(".war, .sz").first.wait_for(timeout=30_000)
        page.wait_for_timeout(300)
        report["screen"] = "war room" if page.locator(".war").count() else "session zero"
        shoot(page, "05-war-room-briefing")
        axe(page, "war-room")
        report["briefing_chars"] = len(page.locator(".tabpanel").inner_text())
        for tab in ("Dossier", "Map", "Ledger"):
            page.get_by_role("tab", name=tab).click()
            page.wait_for_timeout(150)
            shoot(page, f"06-war-room-{tab.lower()}")
            if tab == "Dossier":
                axe(page, "dossier")
                report["dossier_items"] = page.locator(".facts li").count()
        page.get_by_role("tab", name="Briefing").click()
        report["contests"] = page.locator(".contest").count()
        report["means"] = page.locator(".means li").count()
        report["pending_orders"] = page.locator(".op").count()
        report["chronicle"] = page.locator(".chronicle li").count()
        body = page.locator("body").inner_text()
        for word in ("controller", "human", "truth", " AI ", "%"):
            if word in body:
                problems.append(f"rule: page text contains {word!r}")
        page.get_by_role("button", name="Report a bug").click()
        page.get_by_role("heading", name="Report a bug").wait_for()
        shoot(page, "07-bug-dialog")
        browser.close()
    report["problems"] = problems
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
