# Revisionist web client: design source (Rounds 1–3)

Exported 22 October 2026 from the Revisionist design canvas. Includes any edits made in the editor up to the export.

## What's here

- `project/*.dc.html`: one file per screen (59 screens).
- `project/canvas.json`: canvas layout and screen titles (A1 to O2).
- `tokens.css`: colours, type, spacing and radius as CSS custom properties.

Start with `project/Index.dc.html`. It lists every screen by its code.

## Reading the files

Each `.dc.html` file is a Design Component: plain HTML and inline CSS, plus a small template layer.

- `{{name}}`: a value from the `renderVals()` function at the bottom of the file.
- `<sc-for list="{{items}}" as="item">`: repeats its contents once per item.
- `<sc-if value="{{flag}}">`: renders its contents only when the flag is true.
- `<dc-import name="WarRoom" mode="thinking">`: mounts another file (`WarRoom.dc.html`) with props.
- `class Component extends DCLogic`: a small React-style class. `renderVals()` returns the template's data. `state` and `setState` work as in React.

The files need the design tool's runtime (`support.js`) to render, so they won't open directly in a browser. Treat them as a precise spec: the markup, styles, copy and sample data are all real. Port them to the production stack (React or otherwise).

## Shared components

Several screens are the same component with one setting changed. In production, build each one once:

| Component | Setting | Screens |
|---|---|---|
| `WarRoom` | `mode`: default, thinking, season-over, error, mandates, quiet; `tab`: briefing, map, ledger | D1–D6, H2, H3, J1, K1, N3a |
| `OrderFlow` | `step` 1–7 | E1–E7 |
| `Brooke` | `view`: theatre, sector, list | F1, F2, F4, K2 |
| `Counsel` | `room`: individual, cabinet | G1, G2 |
| `SessionZero` | `step` 1–10 | L1–L10 |
| `HostConsole` | `page`: overview, why | M1, M2 |
| `Settings` | `section`: account, agent | O1, O2 |

## Rules the interface never breaks

1. Everything comes from the seat's own view. Unseen means absent.
2. A false fact looks identical to a true one until the Reveal.
3. Other seats are characters only. Nothing shows human, AI, deputy or connected agent.
4. Odds are bands (Impossible, Long shot, Contested, Likely, Assured), shown after filing, never as percentages.
5. Rival goals show only the pieces uncovered.
6. Visibility and certainty always carry an icon and a label, never colour alone.

## Placeholders and sample content

- The quill-dagger mark is a placeholder.
- Portraits are initials in an engraved frame.
- Map coastlines and borders are rough placeholders until a real map data source is chosen.
- Agents (Hartley, Ferris, Pettigrew), reports, ledger figures, costs and dossier contents are invented sample data.
