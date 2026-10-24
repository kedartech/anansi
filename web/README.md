# Revisionist web client

Static ES modules, no framework, no build step. Served with the JSON API by
`revisionist web serve` (src/revisionist/engine/web/server.py); the design source
is docs/web-design/.

    revisionist web serve --host <tailnet-ip> --port 8740   # plain HTTP: private addresses only

- js/rules.js: the design rules (odds as five bands, visibility as icon plus word).
- js/dom.js: the only DOM builder; text nodes only, never innerHTML.
- js/views/: sign-in and recovery, join, Session Zero (player steps), war room.
- fonts/: self-hosted OFL fonts, licences beside them. No third-party requests.
- assets/land-110m.json: Natural Earth 1:110m land (public domain), rounded to 0.1
  degree; the coastlines under the realism map.

Tests:

    npm test                                  # rule guards (node --test)
    python test/e2e/smoke.py BASE CODE OUT    # in the Playwright image; see the file

Map and ledger tabs say "not live yet": the engine has no data for them.
