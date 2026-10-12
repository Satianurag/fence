import json

from fence.evidence import SOURCE_ORDER, Excerpt, focus_terms, intent_excerpts, render, select_turns, transcript_turns


def line(role, content, **kw):
    return json.dumps({"type": role, "message": {"role": role, "content": content}, **kw})


TRANSCRIPT = "\n".join([
    json.dumps({"type": "progress", "data": {"type": "hook_progress"}}),
    line("user", "Move appendCheckpointTrailer into the trailers package"),
    line("assistant", [{"type": "tool_use", "name": "Read", "input": {}}]),
    line("user", [{"type": "tool_result", "content": "file body"}]),
    line("assistant", [{"type": "thinking", "thinking": "blank line before trailer is deliberate"}, {"type": "text", "text": "Done, AppendCheckpointTrailer now lives in trailers.go"}]),
    "not json",
    line("assistant", [{"type": "text", "text": "Unrelated cleanup of README"}]),
])


def test_transcript_keeps_only_spoken_turns():
    turns = transcript_turns(TRANSCRIPT)
    assert [r for r, _ in turns] == ["user", "assistant", "assistant"]
    assert "blank line before trailer is deliberate" in turns[1][1]
    assert all("file body" not in t for _, t in turns)


def test_select_turns_prefers_focus_terms_in_original_order():
    turns = transcript_turns(TRANSCRIPT)
    out = select_turns(turns, ["AppendCheckpointTrailer", "trailers.go"])
    assert "README" not in out
    assert out.startswith("[assistant] ")
    assert "trailers.go" in out


def test_select_turns_falls_back_to_first_user_and_last_agent():
    out = select_turns(transcript_turns(TRANSCRIPT), ["NoSuchSymbol"])
    assert out.splitlines()[0].startswith("[user] Move")
    assert out.splitlines()[-1] == "[assistant] Unrelated cleanup of README"


def test_select_turns_respects_budget():
    turns = [("user", "trailers.go " + "x" * 500)] * 10
    assert len(select_turns(turns, ["trailers.go"], budget=1200)) <= 1200


def test_long_turn_is_clipped_to_a_verbatim_window_around_the_term():
    text = "a" * 3000 + " AppendCheckpointTrailer " + "b" * 3000
    out = select_turns([("assistant", text)], ["AppendCheckpointTrailer"])
    assert "AppendCheckpointTrailer" in out
    assert out.removeprefix("[assistant] ") in text


def test_order_is_message_then_transcript_then_prompt():
    ex = intent_excerpts("abc1234567", "ck1", "body", [("user", "hello")], "the prompt", [])
    assert [e.source for e in ex] == list(SOURCE_ORDER) == ["commit_message", "transcript", "prompt"]


def test_empty_sources_are_skipped():
    ex = intent_excerpts("abc1234567", "ck1", "", [], "the prompt", [])
    assert [e.source for e in ex] == ["prompt"]


def test_focus_terms():
    spans = [["cmd/x/trailers.go", 1, 9, "func:AppendCheckpointTrailer"], ["cmd/x/attach.go", 3, 3, "file:line"]]
    assert focus_terms(spans) == ["AppendCheckpointTrailer", "trailers.go", "attach.go"]


def test_render_numbers_blocks_from_one():
    out = render([Excerpt("commit_message", "c1", "k1", "A"), Excerpt("prompt", "c1", "k1", "B")])
    assert 'id="1" source="commit_message"' in out and 'id="2" source="prompt"' in out
