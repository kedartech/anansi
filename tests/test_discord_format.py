from revisionist.engine.discord.format import bullets, dashboard, goal_offer, history, named
from revisionist.engine.discord.rest import PART_LIMIT, split_message
from revisionist.engine.session_zero import RECORD_HEADER, SUGGESTED


def test_bullets_marks_each_line_and_bolds_headers() -> None:
    assert bullets(["one", "two"]) == "- one\n- two"
    assert bullets([RECORD_HEADER, "Ann: note"]) == f"**{RECORD_HEADER}**\n- Ann: note"


def test_bullets_empty_is_empty() -> None:
    assert bullets([]) == ""


def test_named_bolds_only_the_name() -> None:
    line = "Lorenzo de' Medici (composite) [taken]: a banker. Reach: Florence."
    assert named(line) == "**Lorenzo de' Medici** (composite) [taken]: a banker. Reach: Florence."


def test_named_stops_at_colon_when_no_tag() -> None:
    assert named("Ann: a note: with a colon") == "**Ann**: a note: with a colon"


def test_named_bracket_tag_ends_the_name() -> None:
    assert named("Ann [taken]: pitch") == "**Ann** [taken]: pitch"


def test_named_empty_is_empty() -> None:
    assert named("") == ""


def test_goal_offer_bolds_the_label_and_bullets_it() -> None:
    line = "H1. Hold the river crossing [Lasting] Balance: too easy."
    assert goal_offer(line) == "- **H1** Hold the river crossing [Lasting] Balance: too easy."


def test_goal_offer_keeps_the_letter_of_each_route() -> None:
    assert goal_offer("C2. Win the vote [Undoable]") == "- **C2** Win the vote [Undoable]"
    assert goal_offer("O3. Own the mill [Lasting]") == "- **O3** Own the mill [Lasting]"


def test_goal_offer_leaves_other_lines_alone() -> None:
    assert goal_offer("Choose one with /goal confirm <label>") == (
        "Choose one with /goal confirm <label>"
    )


def test_history_bolds_each_paragraph_label() -> None:
    text = "\n\n".join(
        [
            "As recorded: The river froze.",
            "The goal in history: Cross it.",
            f"{SUGGESTED} H1, the ford.",
            "Where the record is thin: the spring.",
        ]
    )
    assert history(text) == "\n\n".join(
        [
            "**As recorded:** The river froze.",
            "**The goal in history:** Cross it.",
            f"**{SUGGESTED}** H1, the ford.",
            "**Where the record is thin:** the spring.",
        ]
    )


def test_history_keeps_unlabelled_paragraphs() -> None:
    assert history("A plain paragraph.\n\nAs recorded: Next.") == (
        "A plain paragraph.\n\n**As recorded:** Next."
    )


def test_history_empty_is_empty() -> None:
    assert history("") == ""


def test_dashboard_bolds_the_first_line_and_bullets_the_rest() -> None:
    lines = ["Ann, tick 3. Slots 2.", "Means: horses; gold.", "Standing: loyal."]
    assert dashboard(lines) == (
        "**Ann, tick 3. Slots 2.**\n- Means: horses; gold.\n- Standing: loyal."
    )


def test_dashboard_with_only_a_header_has_no_trailing_newline() -> None:
    assert dashboard(["Ann, tick 0. Slots 1."]) == "**Ann, tick 0. Slots 1.**"


def test_dashboard_empty_is_empty() -> None:
    assert dashboard([]) == ""


def test_long_output_splits_under_the_part_limit() -> None:
    lines = [f"Character {i}: " + "a pitch " * 30 + "Reach: far." for i in range(60)]
    parts = split_message(bullets([named(line) for line in lines]))
    assert len(parts) > 1
    assert all(len(part) <= PART_LIMIT for part in parts)
    assert "".join(parts) == bullets([named(line) for line in lines])
