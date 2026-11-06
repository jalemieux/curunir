import numpy as np

from local_voice import SentenceSplitter, resample


def test_splitter_emits_sentences_as_they_complete():
    s = SentenceSplitter()
    assert s.feed("The sky is blue") == []
    assert s.feed(" because of Rayleigh scattering. Shorter wave") == ["The sky is blue because of Rayleigh scattering."]
    assert s.feed("lengths scatter more!\nThat is all.") == ["Shorter wavelengths scatter more!"]
    assert s.flush() == "That is all."
    assert s.flush() is None


def test_splitter_keeps_short_fragments_attached():
    s = SentenceSplitter()
    assert s.feed("Dr. Smith arrived late. Fine.") == ["Dr. Smith arrived late."]
    assert s.flush() == "Fine."


def test_resample_changes_length():
    audio = np.zeros(24000, dtype=np.float32)
    assert len(resample(audio, 24000, 16000)) == 16000
