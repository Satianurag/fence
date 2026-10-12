"""Intent evidence for the commits behind an issue's code.

For each evidence commit the intent text is taken in this order: commit message body, then the
checkpoint transcript (full.jsonl in the checkpoint ref), then the prompt. Excerpts are compact and
carry their source, so they fit an LLM prompt and can be quoted back exactly."""
import json
from dataclasses import asdict, dataclass
from typing import Any

from .resolve import Repo

SOURCE_ORDER = ("commit_message", "transcript", "prompt")
BODY_CHARS = 1500
TRANSCRIPT_CHARS = 2000
TURN_CHARS = 600
PROMPT_CHARS = 800
TOTAL_CHARS = 28000
MAX_SESSIONS = 3

Turn = tuple[str, str]  # role, text


@dataclass(frozen=True)
class Excerpt:
    source: str  # one of SOURCE_ORDER
    commit: str
    checkpoint: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def transcript_turns(jsonl: str) -> list[Turn]:
    """What the human and the agent said, in order. Tool calls, tool results and harness events are dropped."""
    turns: list[Turn] = []
    for line in jsonl.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        msg = o.get("message") if isinstance(o.get("message"), dict) else o
        role = msg.get("role") or o.get("type")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in ("text", "thinking"):
                    parts.append(b.get("text") or b.get("thinking") or "")
        text = "\n".join(p.strip() for p in parts if p and p.strip())
        if text:
            turns.append((role, text))
    return turns


def focus_terms(span_list: list[list[Any]]) -> list[str]:
    """Symbols and file names the issue points at; used to pick the relevant transcript turns."""
    terms: list[str] = []
    for f, _a, _b, how in span_list:
        if how.startswith(("func:", "search:")):
            terms.append(how.split(":", 1)[1])
        terms.append(f.rsplit("/", 1)[-1])
    return list(dict.fromkeys(terms))


def _clip(text: str, limit: int, terms: list[str]) -> str:
    """Cut to `limit` chars, keeping the window around the first focus term. Always a verbatim slice."""
    if len(text) <= limit:
        return text
    hits = [i for i in (text.find(t) for t in terms) if i >= 0]
    start = max(0, min(hits) - limit // 3) if hits else 0
    start = min(start, len(text) - limit)
    return text[start:start + limit]


def select_turns(turns: list[Turn], terms: list[str], budget: int = TRANSCRIPT_CHARS, turn_chars: int = TURN_CHARS) -> str:
    """Turns that mention the most focus terms, within budget, in their original order.
    With no match, fall back to the first human turn and the last agent turn."""
    scored = [(sum(t in text for t in terms), i) for i, (_role, text) in enumerate(turns)]
    picked = [i for s, i in sorted(scored, key=lambda x: (-x[0], x[1])) if s > 0]
    if not picked and turns:
        first_user = next((i for i, (r, _) in enumerate(turns) if r == "user"), 0)
        last_agent = next((i for i in range(len(turns) - 1, -1, -1) if turns[i][0] == "assistant"), first_user)
        picked = list(dict.fromkeys([first_user, last_agent]))
    kept: list[tuple[int, str]] = []
    used = 0
    for i in picked:
        role, text = turns[i]
        piece = f"[{role}] {_clip(text, turn_chars, terms)}"
        if used + len(piece) > budget:
            break
        kept.append((i, piece))
        used += len(piece)
    return "\n".join(p for _, p in sorted(kept))


def intent_excerpts(commit: str, checkpoint: str, body: str, turns: list[Turn], prompt: str, terms: list[str]) -> list[Excerpt]:
    """Excerpts for one commit in SOURCE_ORDER. Empty sources are left out."""
    texts = {
        "commit_message": _clip(body.strip(), BODY_CHARS, terms),
        "transcript": select_turns(turns, terms),
        "prompt": prompt.strip()[:PROMPT_CHARS],
    }
    return [Excerpt(s, commit, checkpoint, texts[s]) for s in SOURCE_ORDER if texts[s]]


def collect(repo: Repo, resolved: dict[str, Any], total_chars: int = TOTAL_CHARS) -> list[Excerpt]:
    """Excerpts for every evidence row of a resolver result, most-blamed commit first, capped in total size."""
    terms = focus_terms(resolved["span_list"])
    commits = {c[:10]: c for c in repo.commit_checkpoints}
    out: list[Excerpt] = []
    used = 0
    for row in resolved["evidence"]:
        ck = row["checkpoint"]
        full = commits.get(row["commit"], row["commit"])
        subject, _date, body = repo.commit_message(full)
        names = repo.checkpoint_files(ck)
        sessions = sorted(n for n in names if n.endswith("/full.jsonl"))[:MAX_SESSIONS]
        turns = [t for n in sessions for t in transcript_turns(repo.checkpoint_blob(ck, n))]
        prompt = repo.checkpoint_blob(ck, "0/prompt.txt") if "0/prompt.txt" in names else ""
        message = subject + ("\n\n" + body if body else "")
        for ex in intent_excerpts(row["commit"], ck, message, turns, prompt, terms):
            if used + len(ex.text) > total_chars:
                return out
            out.append(ex)
            used += len(ex.text)
    return out


def render(excerpts: list[Excerpt]) -> str:
    """Excerpts as numbered blocks for an LLM prompt."""
    return "\n\n".join(
        f"<evidence id=\"{i}\" source=\"{e.source}\" commit=\"{e.commit}\" checkpoint=\"{e.checkpoint}\">\n{e.text}\n</evidence>"
        for i, e in enumerate(excerpts, 1)
    )
