"""The realism routes of the web client: ``/api/me``'s realism block and ``/api/realism/*``.

Same harness as ``tests/test_web.py``. The campaign is the realism toy, seated
straight into Postgres and linked to a web account; no model is called.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest

from revisionist.core import events as ev
from revisionist.core.canary import canaries_hidden_from
from revisionist.core.ids import (
    CampaignId,
    DocumentId,
    EntityId,
    EventId,
    QuantityId,
    SeatId,
    derived_id,
)
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import (
    BodyField,
    DocumentKind,
    DocumentTruth,
    DocumentView,
    TruthRef,
)
from revisionist.core.visibility import PUBLIC
from revisionist.core.world_compile import resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick, session_zero
from revisionist.engine.store import load_state
from revisionist.engine.web.accounts import WebSeats
from tests.test_web import Client, Web, _florence, web  # noqa: F401

CANARY = "CANARY-TRUTH-OIL-51c2"


class Realism:
    """A realism toy game with web accounts seated in it."""

    def __init__(self, web: Web) -> None:  # noqa: F811
        self.web = web
        self.decl = cmp.load_world(cmp.world_path("realism-toy"))
        store = web.app.open()
        try:
            self.campaign: CampaignId = cmp.new_campaign(store, self.decl)
        finally:
            store.close()
        self.ids = resolve_keys(self.decl, self.campaign)

    def seat(self, handle: str, who: str) -> Client:
        """Sign up ``handle`` and seat them as the toy's ``who``."""
        client, _ = self.web.signed_up(handle)
        store = self.web.app.open()
        try:
            seat = SeatId(derived_id(self.campaign, "seat", EntityId(self.ids[who])))
            name = store_state(store, self.campaign).entities[EntityId(self.ids[who])].name
            session_zero.claim(store, self.campaign, seat, name)
            account = client.me()["account"]["handle"]
            row = store.conn.execute(
                "SELECT account_id FROM web_accounts WHERE handle = %s", (account,)
            ).fetchone()
            assert row is not None
            WebSeats(store.conn).link(self.campaign, seat, row[0])
        finally:
            store.close()
        return client

    def open_play(self) -> None:
        store = self.web.app.open()
        try:
            state = store_state(store, self.campaign)
            store.append_all(
                [
                    ev.Event(
                        event_id=EventId(derived_id(self.campaign, "close-zero")),
                        campaign_id=self.campaign,
                        seq=state.last_seq + 1,
                        tick=state.tick,
                        scope=PUBLIC,
                        payload=ev.SessionZeroClosed(),
                        versions=cmp.versions(),
                    )
                ]
            )
        finally:
            store.close()

    def first_tick(self) -> None:
        store = self.web.app.open()
        try:
            realism_tick.run_realism_tick(store, self.campaign)
        finally:
            store.close()

    def plant(self) -> str:
        """A document the seat receives whose truth carries a canary; returns the canary."""
        store = self.web.app.open()
        try:
            state = store_state(store, self.campaign)
            doc_id = DocumentId(derived_id(self.campaign, "doc", "planted"))
            today = state.in_world_date
            assert today is not None
            view = DocumentView(
                doc_id=doc_id,
                kind=DocumentKind.WHIP_COUNT,
                author=EntityId(self.ids["bram"]),
                issued=today,
                arrives=today,
                channel=ChannelKind.LETTER,
                title="Planted count",
                body=(BodyField(label="Votes for the levy", value=8, unit="votes"),),
                prose="The Council is closer than the Speaker admits.",
                scope=PUBLIC,
            )
            truth = DocumentTruth(
                doc_id=doc_id,
                truth_refs=(
                    TruthRef(
                        quantity_id=QuantityId(self.ids["council_votes"]),
                        value=7,
                        canary=CANARY,
                    ),
                ),
            )
            store.append_all(
                [
                    ev.Event(
                        event_id=EventId(derived_id(self.campaign, "plant")),
                        campaign_id=self.campaign,
                        seq=state.last_seq + 1,
                        tick=state.tick,
                        scope=PUBLIC,
                        payload=ev.DocumentIssued(document=view, truth=truth),
                        versions=cmp.versions(),
                    )
                ]
            )
        finally:
            store.close()
        return CANARY

    def canaries(self, who: str) -> list[str]:
        store = self.web.app.open()
        try:
            state = store_state(store, self.campaign)
            seat = SeatId(derived_id(self.campaign, "seat", EntityId(self.ids[who])))
            return list(canaries_hidden_from(state, seat))
        finally:
            store.close()


def store_state(store: Any, campaign: CampaignId) -> Any:
    return load_state(store, campaign)


@pytest.fixture
def solo(web: Web) -> Iterator[tuple[Realism, Client]]:  # noqa: F811
    game = Realism(web)
    client = game.seat("ada", "ada")
    game.open_play()
    game.first_tick()
    yield game, client


def post(client: Client, route: str, /, **body: Any) -> tuple[int, Any]:
    return client.call(f"/api/realism/{route}", body)


def realism_of(client: Client) -> dict[str, Any]:
    view = client.me()["game"]["view"]
    assert "realism" in view, view
    return dict(view["realism"])


def test_me_carries_the_realism_block_for_a_realism_seat(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    game = ada.me()["game"]
    assert game["ruleset"] == "realism"
    block = game["view"]["realism"]
    assert set(block) == {"documents", "desk", "situation", "orders", "clock"}
    assert block["documents"]["documents"] and block["documents"]["unread"] > 0
    assert block["desk"]["levers"]
    # nothing but the horizon is due, and the horizon is not a thing anyone waits on
    assert block["clock"]["world_date"] and block["clock"]["next_world_date"]
    assert block["clock"]["waiting_on"] is None


def test_me_has_no_realism_block_for_a_legacy_seat(web: Web) -> None:  # noqa: F811
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    game = lorenzo.me()["game"]
    assert game["ruleset"] == "legacy"
    assert "realism" not in game["view"]


def test_no_realism_block_in_session_zero(web: Web) -> None:  # noqa: F811
    ada = Realism(web).seat("ada", "ada")
    game = ada.me()["game"]
    assert game["ruleset"] == "realism" and game["phase"] == "session_zero"
    assert "realism" not in game["view"]


def test_read_marks_unread_to_read_and_is_idempotent(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    before = realism_of(ada)["documents"]
    target = next(d for d in before["documents"] if d["unread"])
    status, page = post(ada, "read", doc_id=target["id"])
    assert status == 200 and page["item"]["id"] == target["id"] and not page["item"]["unread"]
    after = realism_of(ada)["documents"]
    assert after["unread"] == before["unread"] - 1
    assert not next(d for d in after["documents"] if d["id"] == target["id"])["unread"]
    assert post(ada, "read", doc_id=target["id"])[0] == 200
    store = game.web.app.open()
    try:
        reads = [e for e in store.read(game.campaign) if isinstance(e.payload, ev.DocumentRead)]
    finally:
        store.close()
    assert len(reads) == 1


def test_a_document_outside_the_seat_scope_is_404(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    assert post(ada, "read", doc_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")[0] == 404


def test_lever_files_an_order_that_appears_in_the_tracker(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    assert realism_of(ada)["orders"]["orders"] == []
    status, out = post(ada, "lever", lever="propose_levy", params={"rate": 3, "purpose": "harbour"})
    assert status == 200 and out["refused"] is False, out
    (order,) = realism_of(ada)["orders"]["orders"]
    assert order["lever"] == "propose_levy" and order["status"] == "moving"
    assert order["params"] == {"rate": 3, "purpose": "harbour"}


def test_lever_refusals_are_replies_not_errors(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    for body in (
        {"lever": "nonesuch", "params": {}},
        {"lever": "propose_levy", "params": {"rate": 999, "purpose": "harbour"}},
        {"lever": "propose_levy", "params": {}},
    ):
        status, out = post(ada, "lever", **body)
        assert status == 200 and out["refused"] is True and out["reply"], out
    assert post(ada, "step", order_id="nope", action="pass")[1]["refused"] is True
    assert post(ada, "step", order_id="nope", action="bogus")[1]["refused"] is True


def test_advance_moves_the_world_date(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    before = realism_of(ada)["clock"]
    status, out = post(ada, "advance")
    assert (
        status == 200
        and out["refused"] is False
        and out["reply"].startswith("The calendar moves to ")
    ), out
    assert realism_of(ada)["clock"]["world_date"] == before["next_world_date"]


def test_advance_is_refused_for_a_shared_game(web: Web) -> None:  # noqa: F811
    game = Realism(web)
    ada = game.seat("ada", "ada")
    game.seat("bram", "bram")
    game.open_play()
    game.first_tick()
    before = realism_of(ada)["clock"]["world_date"]
    status, out = post(ada, "advance")
    assert status == 200 and out == {
        "reply": "Only the host advances a shared game.",
        "refused": True,
    }
    assert realism_of(ada)["clock"]["world_date"] == before


def test_realism_routes_refuse_a_legacy_game_and_one_not_in_play(web: Web) -> None:  # noqa: F811
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    status, out = post(lorenzo, "advance")
    assert status == 400 and "realism" in out["error"]

    zero = Realism(web).seat("ada", "ada")  # no legacy game may be live beside it
    assert zero.me()["game"] is None or post(zero, "advance")[0] == 400


def test_realism_routes_need_a_session_and_this_origin(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    assert post(game.web.client(), "advance")[0] == 401
    status, _ = ada.call("/api/realism/advance", {}, headers={"Origin": "http://evil.example"})
    assert status == 403
    assert post(ada, "nonesuch")[0] == 404


def test_the_planted_truth_canary_never_reaches_a_response(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    canary = game.plant()
    assert canary in game.canaries("ada")
    canaries = [canary]
    seen = [json.dumps(ada.me())]
    for d in realism_of(ada)["documents"]["documents"]:
        seen.append(json.dumps(post(ada, "read", doc_id=d["id"])[1]))
    seen.append(json.dumps(post(ada, "advance")[1]))
    seen.append(json.dumps(ada.me()))
    for text in seen:
        assert not any(c in text for c in canaries)


def test_advance_writes_prose_for_the_pages_it_issues(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    first_day = realism_of(ada)["clock"]["world_date"]
    assert post(ada, "advance")[1]["refused"] is False
    fresh = [d for d in realism_of(ada)["documents"]["documents"] if d["issued"] > first_day]
    assert fresh
    for d in fresh:
        status, page = post(ada, "read", doc_id=d["id"])
        assert status == 200 and page["prose"], page


def test_flag_round_trips_and_is_idempotent(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    listing = realism_of(ada)["documents"]
    assert listing["flagged"] == 0 and not any(d["flagged"] for d in listing["documents"])
    target = listing["documents"][0]["id"]
    assert post(ada, "flag", doc_id=target, flagged=True) == (200, {"flagged": True})
    assert post(ada, "flag", doc_id=target, flagged=True)[0] == 200
    after = realism_of(ada)["documents"]
    assert after["flagged"] == 1
    assert [d["id"] for d in after["documents"] if d["flagged"]] == [target]
    assert post(ada, "read", doc_id=target)[1]["item"]["flagged"] is True
    store = game.web.app.open()
    try:
        flags = [e for e in store.read(game.campaign) if isinstance(e.payload, ev.DocumentFlagged)]
    finally:
        store.close()
    assert len(flags) == 1
    assert post(ada, "flag", doc_id=target, flagged=False)[0] == 200
    assert post(ada, "flag", doc_id=target, flagged=False)[0] == 200
    assert realism_of(ada)["documents"]["flagged"] == 0


def test_flag_is_404_outside_the_seat_scope_and_refuses_a_bad_flag(
    solo: tuple[Realism, Client],
) -> None:
    _, ada = solo
    assert post(ada, "flag", doc_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", flagged=True)[0] == 404
    target = realism_of(ada)["documents"]["documents"][0]["id"]
    status, out = post(ada, "flag", doc_id=target, flagged="yes")
    assert status == 200 and out["refused"] is True


def test_lever_and_param_labels_reach_the_desk_and_the_orders(
    solo: tuple[Realism, Client],
) -> None:
    _, ada = solo
    (lever,) = realism_of(ada)["desk"]["levers"]
    assert lever["label"] == "Propose a levy" and lever["summary"]
    assert [p["label"] for p in lever["params"]] == ["Rate", "Purpose"]
    assert lever["params"][1]["choices"][0] == {"value": "harbour", "label": "For the harbour"}
    post(ada, "lever", lever="propose_levy", params={"rate": 3, "purpose": "harbour"})
    (order,) = realism_of(ada)["orders"]["orders"]
    assert order["label"] == "Propose a levy"
    assert order["terms"] == ["Rate: 3", "Purpose: For the harbour"]


def test_replies_are_in_words(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    _, filed = post(ada, "lever", lever="propose_levy", params={"rate": 3, "purpose": "harbour"})
    assert filed["reply"].startswith("Sent to ") and "Propose a levy" in filed["reply"]
    _, advanced = post(ada, "advance")
    for out in (filed, advanced):
        assert out["refused"] is False, out
        assert "tick" not in out["reply"].lower() and "_" not in out["reply"], out
    assert advanced["reply"].startswith("The calendar moves to ")
    assert "Reason: " in advanced["reply"]


def test_step_replies_cover_every_action_and_status_without_underscores() -> None:
    from revisionist.core.realism.pipeline import OrderStatus, StepAction
    from revisionist.engine.web import server

    assert set(server._DONE) == set(StepAction)
    assert set(server._STANDING) == set(OrderStatus)
    for text in (*server._DONE.values(), *server._STANDING.values()):
        assert "_" not in text and "tick" not in text.lower()


class Aide:
    """Answers every directive with ``output``; no model is called."""

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.prompts: list[str] = []

    def call(self, request: Any) -> Any:
        from revisionist.engine.llm import Reply

        self.prompts.append(request.prompt)
        return Reply(self.output, request.model, 10, 10, 0, 0.0)


def aide(game: Realism, output: dict[str, Any]) -> Aide:
    reader = Aide(output)
    game.web.app.transport_factory = lambda: reader
    return reader


LEVY: dict[str, Any] = {
    "lever": "propose_levy",
    "params": {"rate": 3, "purpose": "harbour"},
    "reason": "",
}
ASK: dict[str, Any] = {
    "to": "Cora Senn",
    "channel": "letter",
    "text": "Put a small harbour levy to the Council.",
}


def directives(game: Realism) -> list[ev.Event]:
    store = game.web.app.open()
    try:
        return [e for e in store.read(game.campaign) if isinstance(e.payload, ev.DirectiveGiven)]
    finally:
        store.close()


def test_contacts_carry_the_channels_a_directive_can_take(solo: tuple[Realism, Client]) -> None:
    _, ada = solo
    contacts = {c["person"]: c["channels"] for c in realism_of(ada)["desk"]["contacts"]}
    assert contacts == {"Bram Tull": ["letter"], "Cora Senn": ["in_person", "letter"]}


def test_a_directive_is_read_shown_and_filed_only_when_signed(
    solo: tuple[Realism, Client],
) -> None:
    game, ada = solo
    reader = aide(game, LEVY)
    status, out = post(ada, "directive", **ASK)
    assert status == 200 and out["refused"] is False, out
    assert out["reply"] == "Your aide reads this as: Propose a levy."
    assert out["proposal"] == {
        "to": "Cora Senn",
        "channel": "letter",
        "text": ASK["text"],
        "lever": "propose_levy",
        "label": "Propose a levy",
        "terms": ["Rate: 3", "Purpose: For the harbour"],
        "params": {"rate": 3, "purpose": "harbour"},
        "reason": None,
    }
    (prompt,) = reader.prompts
    assert prompt.endswith(ASK["text"]) and "## To Cora Senn, by letter" in prompt
    assert realism_of(ada)["orders"]["orders"] == [] and directives(game) == []

    p = out["proposal"]
    status, out = post(ada, "sign", **ASK, lever=p["lever"], params=p["params"])
    assert status == 200 and out == {
        "reply": "Sent to Cora Senn: Propose a levy.",
        "refused": False,
    }
    (order,) = realism_of(ada)["orders"]["orders"]
    assert order["params"] == {"rate": 3, "purpose": "harbour"}
    (record,) = directives(game)
    assert isinstance(record.payload, ev.DirectiveGiven)
    assert record.payload.text == ASK["text"] and record.payload.order_id == order["id"]
    assert record.scope.visibility.value == "private"


def test_what_cannot_be_done_is_a_reason_and_files_nothing(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    aide(game, {"lever": "", "params": {}, "reason": "The Council alone can dissolve itself."})
    status, out = post(ada, "directive", **ASK | {"text": "Dissolve the Council."})
    assert status == 200 and out["refused"] is True
    assert out["reply"] == "The Council alone can dissolve itself."
    assert out["proposal"]["lever"] is None and out["proposal"]["terms"] == []
    aide(
        game, {"lever": "propose_levy", "params": {"rate": 99, "purpose": "harbour"}, "reason": ""}
    )
    status, out = post(ada, "directive", **ASK)
    assert out["refused"] is True and "_" not in out["reply"]
    assert realism_of(ada)["orders"]["orders"] == [] and directives(game) == []


def test_directive_refusals_are_replies(solo: tuple[Realism, Client]) -> None:
    game, ada = solo
    reader = aide(game, LEVY)
    for body in (
        ASK | {"to": "Nobody at all"},
        ASK | {"to": "Bram Tull", "channel": "in_person"},
        ASK | {"channel": "pigeon"},
        ASK | {"text": "   "},
    ):
        status, out = post(ada, "directive", **body)
        assert status == 200 and out["refused"] is True and out["reply"], (body, out)
        assert "_" not in out["reply"]
    assert reader.prompts == []
    for signed in (
        ASK | {"lever": "nonesuch", "params": {}},
        ASK | {"lever": "propose_levy", "params": {"rate": 99, "purpose": "harbour"}},
        ASK | {"to": "Nobody at all", **LEVY},
    ):
        status, out = post(ada, "sign", **signed)
        assert status == 200 and out["refused"] is True, (signed, out)
    assert realism_of(ada)["orders"]["orders"] == [] and directives(game) == []


def test_an_unreachable_aide_points_to_the_standard_forms(solo: tuple[Realism, Client]) -> None:
    from revisionist.engine.llm import TransportError

    class Down:
        def call(self, request: Any) -> Any:
            raise TransportError("model unavailable")

    game, ada = solo
    game.web.app.transport_factory = Down
    status, out = post(ada, "directive", **ASK)
    assert status == 200 and out["refused"] is True and "standard forms" in out["reply"]


def test_a_directive_reply_naming_a_hidden_canary_is_withheld(
    solo: tuple[Realism, Client],
) -> None:
    game, ada = solo
    canary = game.plant()
    aide(game, {"lever": "", "params": {}, "reason": f"The truth is {canary}."})
    status, out = post(ada, "directive", **ASK)
    assert status == 200 and out["refused"] is True and canary not in json.dumps(out)
    status, out = post(ada, "directive", **ASK | {"text": f"Say {canary} to the Council."})
    assert status == 200 and "error" in out and canary not in json.dumps(out)
