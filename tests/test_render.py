"""Tests for onboarding/render.py — questionnaire → identity.md rendering."""

from onboarding.render import (
    PERSONAS,
    parse_answers,
    render_boundaries,
    render_identity,
    render_personality,
    render_style,
    render_use_cases_section,
)


# ---------- parse_answers ----------

QUESTIONS_FILLED = """\
# Onboarding Questions

Some intro text.

---

## About you

### 1. What should curunir call you?
Your name.

_Answer:_ Alice

### 2. What do you do?

_Answer:_
I run a small consultancy.
We help startups scope MVPs.

---

## Style

### 5. Response style?

_Answer:_  Terse / direct

### 7. Personality?

_Answer:_ Pragmatic peer

### 8. Anything else?

_Answer:_
"""


def test_parses_inline_and_block_answers():
    answers = parse_answers(QUESTIONS_FILLED)
    assert answers[1] == "Alice"
    assert answers[2] == "I run a small consultancy.\nWe help startups scope MVPs."
    assert answers[5] == "Terse / direct"
    assert answers[7] == "Pragmatic peer"


def test_skips_empty_answers():
    answers = parse_answers(QUESTIONS_FILLED)
    assert 8 not in answers  # Q8 has empty body
    assert 3 not in answers  # Q3 wasn't in the input
    assert 4 not in answers


def test_handles_no_answers_at_all():
    text = "### 1. Q\n\n_Answer:_\n\n### 2. Q\n\n_Answer:_   \n"
    assert parse_answers(text) == {}


# ---------- render_personality ----------

def test_persona_named_match_normalizes_whitespace_case_hyphens():
    expected = PERSONAS["pragmaticpeer"]
    assert render_personality("Pragmatic peer") == expected
    assert render_personality("pragmatic-peer") == expected
    assert render_personality("PRAGMATIC_PEER") == expected
    assert render_personality("pragmaticpeer") == expected


def test_persona_freeform_passes_through():
    assert render_personality("calm and witty like a botanist") == (
        "calm and witty like a botanist"
    )


def test_persona_default_is_pragmatic_peer():
    assert render_personality(None) == PERSONAS["pragmaticpeer"]
    assert render_personality("") == PERSONAS["pragmaticpeer"]


def test_all_six_personas_have_expansions():
    expected_keys = {
        "pragmaticpeer",
        "executiveassistant",
        "stoicbutler",
        "friendlyconcierge",
        "wittycompanion",
        "chiefofstaff",
    }
    assert expected_keys == set(PERSONAS.keys())
    for v in PERSONAS.values():
        assert len(v) > 30  # each is a real sentence, not a stub


# ---------- render_style ----------

def test_style_keyword_matching():
    assert "Minimum words" in render_style("Terse / direct")
    assert "Minimum words" in render_style("just be terse please")
    assert "Natural" in render_style("Conversational")
    assert "Walk through" in render_style("Detailed / explanatory")


def test_style_default_is_conversational():
    assert "Natural" in render_style(None)
    assert "Natural" in render_style("")


def test_style_freeform_passes_through():
    assert render_style("speak in haiku") == "speak in haiku"


# ---------- render_use_cases_section ----------

def test_use_cases_omitted_when_empty():
    assert render_use_cases_section("Alice", None) == ""
    assert render_use_cases_section("Alice", "") == ""


def test_use_cases_bulletizes_comma_list():
    out = render_use_cases_section("Alice", "email triage, research, scheduling")
    assert "- email triage" in out
    assert "- research" in out
    assert "- scheduling" in out
    assert "What Alice wants help with" in out


def test_use_cases_bulletizes_newline_list():
    out = render_use_cases_section("Alice", "- email triage\n- research\n- scheduling")
    assert "- email triage" in out
    assert "- research" in out


# ---------- render_boundaries ----------

def test_boundaries_default_when_empty():
    out = render_boundaries(None)
    assert "Always confirm before" in out
    assert out.startswith("- ")


def test_boundaries_bulletizes_user_input():
    out = render_boundaries("never email anyone\nnever spend money")
    assert "- never email anyone" in out
    assert "- never spend money" in out


# ---------- render_identity (end-to-end) ----------

TEMPLATE = """\
# Curunir

You are curunir, a personal assistant to {{name}}.{{name_hint}}

## Personality
{{personality}}

## About {{name}}
{{about}}
{{use_cases_section}}
## Communication style
{{communication}}

## Before you act
{{boundaries}}
"""


def test_full_render_with_all_answers():
    answers = {
        1: "Alice",
        2: "I run a consultancy.",
        3: "email, research, scheduling",
        4: "America/New_York",
        5: "Terse / direct",
        6: "never email anyone without asking",
        7: "Pragmatic peer",
        8: "I prefer Sunday for planning.",
    }
    out = render_identity(answers, TEMPLATE)
    assert "personal assistant to Alice" in out
    assert "America/New_York" in out
    assert "I run a consultancy." in out
    assert "I prefer Sunday for planning." in out
    assert "What Alice wants help with" in out
    assert "- email" in out
    assert "Be direct, dry, and opinionated" in out
    assert "Minimum words" in out
    assert "- never email anyone without asking" in out
    assert "*Note:" not in out  # no name hint when name is provided


def test_full_render_with_no_answers_uses_defaults():
    out = render_identity({}, TEMPLATE)
    assert "personal assistant to the user" in out
    assert "*Note:" in out  # name hint is shown
    assert "you don't yet know the user's name" in out
    assert "UTC" in out  # default timezone
    assert "Be direct, dry, and opinionated" in out  # default persona
    assert "Natural" in out  # default style
    assert "Always confirm before" in out  # default boundary
    assert "What the user wants help with" not in out  # use cases section omitted


def test_render_writes_no_unfilled_placeholders():
    out = render_identity({1: "Alice"}, TEMPLATE)
    assert "{{" not in out
    assert "}}" not in out
