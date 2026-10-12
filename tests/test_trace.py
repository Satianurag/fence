import pytest

from fence.trace import MAX_FIELD_CHARS, Tracer, llm_span
from fence.verdict import LLMCall

CALL = LLMCall(model="claude-sonnet-5-5", prompt="p", output="o", input_tokens=5, output_tokens=7, cache_read_tokens=11, cache_write_tokens=13, cost_usd=0.02, duration_ms=40)


def build():
    t = Tracer("fence.investigate", "Anurag's project", input={"issue": 1})
    with t.step("locate") as s:
        s.output = {"spans": 1}
    with t.step("verdict") as s:
        s.children = [llm_span(CALL)]
    return t.payload()


def test_span_tree_shape():
    p = build()
    assert (p["name"], p["kind"], p["project"], p["status"]) == ("fence.investigate", "WORKFLOW", "Anurag's project", "OK")
    assert [(c["name"], c["kind"], c["tool_name"]) for c in p["children"]] == [("locate", "TOOL", "locate"), ("verdict", "TOOL", "verdict")]
    assert all("project" not in c for c in p["children"])
    assert "children" not in p["children"][0]


def test_llm_span_fields():
    llm = build()["children"][1]["children"][0]
    assert (llm["kind"], llm["model"]) == ("LLM", "claude-sonnet-5-5")
    assert llm["tokens"] == {"prompt": 29, "completion": 7}
    assert llm["attributes"] == {"neatlogs.llm.provider": "anthropic", "neatlogs.llm.token_count.cache_read": 11, "neatlogs.llm.token_count.cache_write": 13}
    assert all(k.startswith("neatlogs.") for k in llm["attributes"])


def test_failed_step_is_error_with_message_and_reraises():
    t = Tracer("run", "proj")
    with pytest.raises(ValueError):
        with t.step("evidence"):
            raise ValueError("boom")
    p = t.payload()
    step = p["children"][0]
    assert (step["status"], step["error"]) == ("ERROR", "ValueError: boom")
    assert (p["status"], p["error"]) == ("ERROR", "evidence failed")


def test_failed_llm_call_is_an_error_span():
    s = llm_span(LLMCall(model="m", prompt="p", error="invalid verdict: x")).to_payload()
    assert (s["status"], s["error"]) == ("ERROR", "invalid verdict: x")


def test_large_fields_are_truncated():
    t = Tracer("run", "proj")
    with t.step("evidence", input="x" * (MAX_FIELD_CHARS + 50)):
        pass
    assert t.payload()["children"][0]["input"].endswith("[50 more chars]")


def test_body_is_gzipped_json_and_shrinks_to_fit():
    import gzip, json, random
    random.seed(1)
    noise = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789 ") for _ in range(60000))  # compresses poorly
    t = Tracer("run", "proj")
    with t.step("verdict") as s:
        s.children = [llm_span(LLMCall(model="m", prompt=noise, output="o"))]
    body = t.body(max_bytes=5000)
    assert len(body) <= 5000
    p = json.loads(gzip.decompress(body))
    assert p["project"] == "proj" and "more chars]" in p["children"][0]["children"][0]["input"]


def test_send_retries_a_refused_body_with_a_smaller_one(monkeypatch):
    import random
    from fence import trace
    random.seed(2)
    noise = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789 ") for _ in range(9000))
    t = Tracer("run", "proj")
    with t.step("verdict", input=noise):
        pass
    sizes = []

    def fake_post(body, endpoint, key):
        sizes.append(len(body))
        if len(sizes) == 1:
            raise trace.Rejected("Neatlogs returned HTTP 403")
        return "abc", 2
    monkeypatch.setattr(trace, "post", fake_post)
    assert trace.send(t, "https://example.invalid", "k") == ("abc", 2)
    assert len(sizes) == 2 and sizes[1] < sizes[0]
