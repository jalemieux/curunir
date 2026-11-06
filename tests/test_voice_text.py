# tests/test_voice_text.py
import pytest

from src.voice.text import SentenceChunker, speakable


def run(deltas, **kw):
    c = SentenceChunker(**kw)
    out = []
    for d in deltas:
        out += c.feed(d)
    tail = c.flush()
    return out + ([tail] if tail else [])


def test_sentences_flush_as_they_complete():
    c = SentenceChunker()
    assert c.feed("The sky is blue") == []
    assert c.feed(" because of Rayleigh scattering. Shorter wave") == ["The sky is blue because of Rayleigh scattering."]
    assert c.feed("lengths scatter more!\nThat is all.") == ["Shorter wavelengths scatter more!"]
    assert c.flush() == "That is all."
    assert c.flush() is None


def test_first_chunk_cuts_at_a_clause_break_after_eight_words():
    out = run(["Let me check your calendar for tomorrow morning, ", "and then I will look at the weather and the traffic on your route."])
    assert out[0] == "Let me check your calendar for tomorrow morning,"
    assert out[1] == "and then I will look at the weather and the traffic on your route."


def test_clause_rule_only_applies_to_the_first_chunk():
    out = run(["Short one. Then a long second sentence, with a comma after eight words, that keeps going until the end."])
    assert out == ["Short one.", "Then a long second sentence, with a comma after eight words, that keeps going until the end."]


def test_no_clause_cut_before_eight_words():
    assert run(["Yes, I can. Done."]) == ["Yes, I can.", "Done."]


@pytest.mark.parametrize("text,expected", [
    ("Dr. Smith arrived late. Fine.", ["Dr. Smith arrived late.", "Fine."]),
    ("See e.g. the notes. Then go.", ["See e.g. the notes.", "Then go."]),
    ("J. Doe wrote it. Really.", ["J. Doe wrote it.", "Really."]),
    ('He said "stop." Then he left.', ['He said "stop."', "Then he left."]),
    ("Line one\nLine two", ["Line one", "Line two"]),
])
def test_abbreviations_quotes_and_newlines(text, expected):
    assert run([text]) == expected


def test_token_by_token_matches_whole_text():
    text = "First things first, we need the file, then we parse it. Second: run the tests! Done?"
    whole = run([text])
    tokens = run(list(text))
    assert tokens == whole == ["First things first, we need the file, then we parse it.", "Second: run the tests!", "Done?"]


@pytest.mark.parametrize("md,spoken", [
    ("Here is the fix:\n```python\nprint(1)\n```\nTry it.", "Here is the fix:\nI've put the code in the chat.\nTry it."),
    ("Run `pytest tests/` first.", "Run pytest tests/ first."),
    ("| a | b |\n|---|---|\n| 1 | 2 |\nThat is the table.", "That is the table."),
    ("## Plan\n- step one\n- step two\n1. third", "Plan\nstep one\nstep two\nthird"),
    ("See [the docs](https://example.com/x) or https://example.com/y now.", "See the docs or a link now."),
    ("This is **bold**, *italic* and ~~gone~~.", "This is bold, italic and gone."),
    ("Great job 🎉 see you ☀️ tomorrow", "Great job see you tomorrow"),
    ("Too   many    spaces\n\n\n\nand lines", "Too many spaces\n\nand lines"),
])
def test_speakable(md, spoken):
    assert speakable(md) == spoken
