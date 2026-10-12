"""Neatlogs conditional detections that fire on Fence's own runs.

These are printed, not created: creating a detection needs dashboard rights, so the project owner adds them once.
The shape (runOn, config.root with group/rule nodes, the `status` field) is copied from the built-in
"Execution Failed" detection as returned by `neatlogs detections get`."""
from typing import Any


def _rule(field: str, operator: str, value: Any) -> dict[str, Any]:
    return dict(type="rule", field=field, operator=operator, value=value)


def _group(logic: str, *children: dict[str, Any]) -> dict[str, Any]:
    return dict(type="group", logic=logic, children=list(children))


def configs() -> list[dict[str, Any]]:
    return [
        dict(
            displayName="Fence asked a human",
            description="Fence stopped instead of changing code: the verdict was decision or no_record and a question was written for a maintainer.",
            type="conditional",
            groupName="Neutral",
            threshold=0.5,
            # the fence.ask span exists only when Fence asks, so its name plus status OK is the whole condition
            runOn=dict(frequency="span_type", span_types=["TOOL", "CHAIN"], span_names=["fence.ask"]),
            config=dict(conditions=None, logic=None, root=_group("AND", _rule("status", "=", "OK"))),
            unverified_alternative=dict(
                note="Same condition read from span metadata instead of the span name. The metadata field path was not confirmed "
                     "against the Neatlogs rule engine; check it in the dashboard's field picker before using it.",
                root=_group("AND", _rule("status", "=", "OK"),
                            _group("OR", _rule("metadata.verdict", "=", "decision"), _rule("metadata.verdict", "=", "no_record"))),
            ),
        ),
        dict(
            displayName="Fence fix failed verification",
            description="The worker's change was rejected by Fence's own verification: a regression, a test that does not reproduce "
                        "the issue, or a modified pre-existing test (existing_test_modified).",
            type="conditional",
            groupName="Negative",
            threshold=0.5,
            # every rejection, existing_test_modified included, marks the fence.verify span ERROR
            runOn=dict(frequency="span_type", span_types=["TOOL"], span_names=["fence.verify"]),
            config=dict(conditions=None, logic=None, root=_group("AND", _rule("status", "=", "ERROR"))),
            unverified_alternative=dict(
                note="Narrower variant for modified tests only; metadata field path not confirmed.",
                root=_group("AND", _rule("status", "=", "ERROR"), _rule("metadata.reason", "=", "existing_test_modified")),
            ),
        ),
    ]


def create_commands() -> list[str]:
    """The `neatlogs detections create` call for each config, for someone whose login may create detections."""
    import json
    import shlex
    out = []
    for c in configs():
        out.append("neatlogs detections create --group <group-uuid-for-%s> --type conditional --name %s --description %s --threshold %s --run-on %s --config %s" % (
            c["groupName"], shlex.quote(c["displayName"]), shlex.quote(c["description"]), c["threshold"],
            shlex.quote(json.dumps(c["runOn"])), shlex.quote(json.dumps(c["config"]))))
    return out
