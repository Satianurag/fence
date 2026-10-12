"""Settings from the environment, with .env as a fallback. Values are never printed or logged."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = "https://ingest.neatlogs.com"
DEFAULT_PROJECT = "Anurag's project"


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip().removeprefix("export ").strip()] = v
    return out


@dataclass(frozen=True)
class Config:
    target: str | None
    model: str
    neatlogs_endpoint: str
    neatlogs_project: str
    neatlogs_api_key: str | None = field(repr=False)  # repr=False keeps the key out of logs and tracebacks


def load(env_file: Path | None = None, real_runs: bool = True) -> Config:
    """The process environment wins over the .env file.
    Real runs (investigate, fix, eval) go to the project named by NEATLOGS_RUNS_PROJECT with NEATLOGS_RUNS_API_KEY
    when both are set, so they stay apart from smoke tests and probes in the default project."""
    env = {**read_env_file(env_file or ROOT / ".env"), **os.environ}
    if real_runs and env.get("NEATLOGS_RUNS_API_KEY") and env.get("NEATLOGS_RUNS_PROJECT"):
        env = {**env, "NEATLOGS_API_KEY": env["NEATLOGS_RUNS_API_KEY"], "NEATLOGS_PROJECT": env["NEATLOGS_RUNS_PROJECT"]}
    return Config(
        target=env.get("FENCE_TARGET") or None,
        model=env.get("FENCE_MODEL") or "sonnet",
        neatlogs_endpoint=(env.get("NEATLOGS_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/"),
        neatlogs_project=env.get("NEATLOGS_PROJECT") or DEFAULT_PROJECT,
        neatlogs_api_key=env.get("NEATLOGS_API_KEY") or None,
    )
