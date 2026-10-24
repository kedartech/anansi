# The realism room as one surface (proposal, built as option A)

Josh's notes 6 to 9 after playing `feat/realism`: the five tabs (In-tray,
Situation, Desk, Orders, Map) split material that one action needs. A directive
is written on the Desk from what an in-tray paper says; a decision due on an
order is taken on the Orders tab; the aide and consult were not in the web room
at all; the map tab was hidden for games begun before positions were kept.

Goal: one view, with the Desk as the working surface. Nothing in it shows more
than the seat's own papers support: every panel is built from the same
`view.realism` block and the same per-seat routes as before.

## Option A: three columns (built)

```
+----------------------------------------------------------------------------------+
| [mark] Game name              | 12 October 1941 · next wake ... | [Advance] [Game]|
|        Playing as Franklin D. Roosevelt, President of the United States   [you] |
+----------------------------------------------------------------------------------+
| (status line: "The wake is running…" while busy)                                  |
+--------------------------+------------------------------------+------------------+
| PAPERS                   | DESK                               | YOUR AIDE        |
| [In-tray 7][Situation]   |  Office, answerable to ...         |  [Brief me]      |
| [Map]          [Widen]   |  Write a directive                 |  summary ...     |
|                          |   To: (o) Cordell Hull             |  Consult         |
|  folder  folder          |           Secretary of State       |   To [..] [Ask]  |
|  folder  folder          |       ( ) Harold Stark             |   past answers   |
|                          |           Chief of Naval Operations|------------------|
|  (an opened paper reads  |   By [..]  Instruction [.....]     | PENDING ORDERS   |
|   here, in its folder,   |   [Prepare the order]              |  ! Lend-Lease .. |
|   with Close folder)     |  Standard forms ...                |    [decide]      |
|                          |  Powers, limits, contacts          |  Escort order .. |
+--------------------------+------------------------------------+------------------+
```

- Left, **Papers**: In-tray, Situation and Map share one reference panel with a
  segmented switch. Opening a paper (from a folder, a situation card or a map
  mark) shows it in this panel, so the desk stays in view while the player
  writes. "Widen" gives the panel the centre's width for the map or a long
  paper; the desk stays one click away.
- Centre, **Desk**: unchanged content, recipient picker shows name and title.
- Right: **Your aide** (brief on demand, consult a contact, earlier answers) on
  top, **Pending orders** below with decisions due first and the step form
  inline.
- Under 1100px the columns stack: Papers, Desk, Aide, Orders.

## Option B: desk with drawers

```
+-------------------------------------------------------------+
| header ...                                                   |
+-----------------------------------------------+-------------+
| [In-tray] [Situation] [Map]  <- open drawers  | PENDING     |
|                                               | ORDERS      |
|  DESK (full width)                            |             |
|                                               | YOUR AIDE   |
|                         +---------------------+             |
|                         | drawer: in-tray     |             |
|                         | slides over the desk|             |
+-------------------------+---------------------+-------------+
```

Wider desk, but a drawer covers part of it, which is the "material in another
tab" problem again in a smaller form.

## Option C: map as the ground

```
+-------------------------------------------------------------+
| header ...                                                   |
+-------------------------------------------------------------+
|  MAP (full bleed)                     +-------------------+ |
|                                       | floating desk     | |
|      marks ...                        | papers / aide     | |
|                                       +-------------------+ |
+-------------------------------------------------------------+
```

Strong look, but many realism worlds have no positions (generated casts, games
begun before the map), so the map cannot be the ground for every game.

## Why A

It is the conservative one: each existing component (folder grid, paper,
desk, tracker, map) moves into a column unchanged; no overlay hides the desk;
it works for a game with no map. B and C stay open if play shows the desk
needs more width.

## Questions for Josh

- Should the aide brief itself on each wake (one model call per wake per
  seat), or only when asked, as built?
- Should pending orders show only those needing a decision, or all moving
  orders (built: decisions first, then the rest still moving; finished ones
  under "Done").
