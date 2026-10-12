from pathlib import Path

from fence import config, detections
from fence.cli import root_metadata


def test_detection_configs_use_the_builtin_shape():
    asked, failed = detections.configs()
    for c in (asked, failed):
        assert c["type"] == "conditional" and c["config"]["root"]["type"] == "group" and c["config"]["root"]["logic"] == "AND"
        assert all(r["type"] == "rule" and r["field"] == "status" for r in c["config"]["root"]["children"])
    assert asked["runOn"]["span_names"] == ["fence.ask"] and asked["config"]["root"]["children"][0]["value"] == "OK"
    assert failed["runOn"]["span_names"] == ["fence.verify"] and failed["config"]["root"]["children"][0]["value"] == "ERROR"
    assert len(detections.create_commands()) == 2


def test_root_metadata_is_flat_and_complete():
    run = dict(issue=7, verdict=dict(verdict="decision", confidence=0.8), outcome="ask", located=dict(located_by="named"),
               cost=dict(usd=0.02, llm_calls=1), mode="fence", action="not_fixed", attempts=[], error=None)
    cfg = config.Config(target="/t", model="sonnet", neatlogs_endpoint="e", neatlogs_project="p", neatlogs_api_key="secret")
    meta = root_metadata(run, cfg)
    assert meta == dict(issue=7, verdict="decision", confidence=0.8, outcome="ask", located_by="named", cost_usd=0.02, llm_calls=1,
                        model="sonnet", mode="fence", action="not_fixed", attempts=0, error=False)
    assert "secret" not in repr(meta) and "secret" not in repr(cfg)


def env_file(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(text)
    return p


def test_runs_project_is_preferred_for_real_runs(tmp_path, monkeypatch):
    for k in ("NEATLOGS_API_KEY", "NEATLOGS_PROJECT", "NEATLOGS_RUNS_API_KEY", "NEATLOGS_RUNS_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    f = env_file(tmp_path, "NEATLOGS_API_KEY=default-key\nNEATLOGS_PROJECT=scratch\nNEATLOGS_RUNS_API_KEY=runs-key\nNEATLOGS_RUNS_PROJECT=fence runs\n")
    real, smoke = config.load(f), config.load(f, real_runs=False)
    assert (real.neatlogs_project, real.neatlogs_api_key) == ("fence runs", "runs-key")
    assert (smoke.neatlogs_project, smoke.neatlogs_api_key) == ("scratch", "default-key")


def test_runs_override_needs_both_values(tmp_path, monkeypatch):
    for k in ("NEATLOGS_API_KEY", "NEATLOGS_PROJECT", "NEATLOGS_RUNS_API_KEY", "NEATLOGS_RUNS_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    cfg = config.load(env_file(tmp_path, "NEATLOGS_API_KEY=default-key\nNEATLOGS_RUNS_PROJECT=fence runs\n"))
    assert (cfg.neatlogs_project, cfg.neatlogs_api_key) == (config.DEFAULT_PROJECT, "default-key")
