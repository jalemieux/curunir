from server import Turn, to_server_time


def test_durations_only_for_known_pairs():
    t = Turn(n=3, speech_end=1000.0, tool_call=1200.0, glm_start=1210.0, glm_first_token=1900.0,
             glm_done=3000.0, first_audio=3400.0, playback_start=3450.4)
    assert t.durations() == {
        "speech_end_to_tool_call": 200,
        "glm_first_token": 690,
        "glm_total": 1790,
        "glm_done_to_first_audio": 400,
        "speech_end_to_first_audio": 2400,
        "first_audio_to_playback": 50,
        "speech_end_to_playback": 2450,
    }


def test_gemini_only_turn_has_no_glm_stages():
    t = Turn(n=1, speech_end=10.0, first_audio=510.0)
    assert t.durations() == {"speech_end_to_first_audio": 500}
    assert t.summary()["type"] == "turn"
    assert t.summary()["n"] == 1


def test_browser_time_maps_to_server_clock():
    assert to_server_time(5000.0, -120.5) == 4879.5
