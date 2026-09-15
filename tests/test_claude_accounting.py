"""Acceptance cases for ACC-01–05, using synthetic native JSONL records."""

from copy import deepcopy
import json
from pathlib import Path

from ahl.harnesses.claude import ClaudeAdapter


BASE_USAGE = {
    "input_tokens": 10,
    "output_tokens": 208,
    "cache_creation_input_tokens": 30,
    "cache_read_input_tokens": 40,
}
CACHE_USAGE = {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20}


def response(identity="response-1", *, usage=None, stop_reason="end_turn", **fields):
    return {
        "type": "assistant",
        "message": {
            "id": identity,
            "role": "assistant",
            "content": [{"type": "text", "text": "Synthetic answer"}],
            "stop_reason": stop_reason,
            "usage": deepcopy(usage) if usage is not None else {
                **BASE_USAGE, "cache_creation": dict(CACHE_USAGE),
            },
        },
        **fields,
    }


def write_session(run_dir, records, source="-workspace/session.jsonl"):
    path = run_dir / "claude" / "projects" / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def accounting(summary):
    return {key: summary[key] for key in (
        "usage", "api_responses", "incomplete_api_responses",
    )}


def test_repeated_blocks_reconcile_complementary_snapshots_and_early_replays(tmp_path):
    early = response(usage={
        "input_tokens": 10, "output_tokens": 2,
        "cache_creation_input_tokens": 3, "cache_read_input_tokens": 40,
        "cache_creation": {"ephemeral_5m_input_tokens": 1},
    }, stop_reason=None)
    final = response()
    del final["message"]["usage"]["input_tokens"]
    final["message"]["usage"]["cache_read_input_tokens"] = 4
    tool = deepcopy(early)
    tool["message"]["content"] = [
        {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {}},
    ]
    # Neither the first, last, nor largest-output snapshot has all counters.
    write_session(tmp_path, [early, final, tool])

    trace = ClaudeAdapter().parse_trace(tmp_path)
    expected = {
        "usage": {**BASE_USAGE, "cache_creation": CACHE_USAGE},
        "api_responses": 1,
        "incomplete_api_responses": 0,
    }
    assert accounting(trace["totals"]) == expected
    assert accounting(trace["sessions"][0]) == expected
    assert len(trace["sessions"][0]["messages"]) == 2
    assert trace["sessions"][0]["summary"]["tool_calls"] == [
        {"id": "tool-1", "name": "Read", "args": {}},
    ]


def test_shared_history_is_reconciled_across_parent_and_subagent_files(tmp_path):
    early = response("shared", usage={"output_tokens": 2}, stop_reason=None)
    write_session(tmp_path, [early, response("parent")], "-workspace/parent.jsonl")
    write_session(tmp_path, [response("shared"), response("shared"), response("child")],
                  "-workspace/parent/subagents/child.jsonl")

    adapter = ClaudeAdapter()
    trace = adapter.parse_trace(tmp_path)
    sessions = {session["file"]: session for session in trace["sessions"]}
    assert sessions["parent.jsonl"]["api_responses"] == 2
    assert sessions["parent.jsonl"]["incomplete_api_responses"] == 1
    assert sessions["parent.jsonl"]["usage"]["output_tokens"] == 210
    assert sessions["parent.jsonl"]["usage"]["cache_creation"] == {
        key: None for key in CACHE_USAGE
    }
    assert sessions["child.jsonl"]["api_responses"] == 2
    assert sessions["child.jsonl"]["incomplete_api_responses"] == 0
    assert sessions["child.jsonl"]["usage"]["output_tokens"] == 416
    assert accounting(trace["totals"]) == {
        "api_responses": 3,
        "incomplete_api_responses": 0,
        "usage": {
            **{key: value * 3 for key, value in BASE_USAGE.items()},
            "cache_creation": {key: value * 3 for key, value in CACHE_USAGE.items()},
        },
    }
    # A reused adapter must scope identities to each run.
    other = tmp_path / "other-run"
    write_session(other, [response("shared", usage={"output_tokens": 1})])
    assert adapter.parse_trace(other)["totals"]["usage"]["output_tokens"] == 1


def test_response_identity_fallbacks_are_namespaced_and_use_source_lines(tmp_path):
    records = [
        response("same", uuid="ignored"), response("same", uuid="other"),
        response(None, uuid="same"), response("", uuid="same"),
        response(None), response([], uuid=[]),
    ]
    write_session(tmp_path, records, "one/session.jsonl")
    # Align the anonymous records' line numbers to expose basename collisions.
    other = write_session(tmp_path, [response(None), response(None, uuid="same")],
                          "two/session.jsonl")
    other.write_text("\n" * 4 + other.read_text())
    trace = ClaudeAdapter().parse_trace(tmp_path)
    assert [session["api_responses"] for session in trace["sessions"]] == [4, 2]
    assert trace["totals"]["api_responses"] == 5
    assert trace["totals"]["usage"]["output_tokens"] == 5 * 208


def test_invalid_counters_remain_unknown_until_valid_zero_is_recorded(tmp_path):
    # One mixed snapshot exercises meaningful invalid-value classes together.
    # True matters because Python treats booleans as integers.
    usage = {
        "input_tokens": True, "output_tokens": -1,
        "cache_creation_input_tokens": 1.5, "cache_read_input_tokens": "2",
        "cache_creation": {"ephemeral_5m_input_tokens": None, "ephemeral_1h_input_tokens": -1},
    }
    write_session(tmp_path, [response(usage=usage)])
    totals = ClaudeAdapter().parse_trace(tmp_path)["totals"]
    assert accounting(totals) == {
        "api_responses": 1,
        "incomplete_api_responses": 1,
        "usage": {
            **dict.fromkeys(BASE_USAGE, 0),
            "cache_creation": dict.fromkeys(CACHE_USAGE, None),
        },
    }
    # A later valid zero completes usage; malformed replays cannot erase it.
    zero = {**dict.fromkeys(BASE_USAGE, 0), "cache_creation": dict.fromkeys(CACHE_USAGE, 0)}
    write_session(tmp_path, [response(usage=usage), response(usage=zero), response(usage=usage)])
    totals = ClaudeAdapter().parse_trace(tmp_path)["totals"]
    assert accounting(totals) == {
        "usage": zero, "api_responses": 1, "incomplete_api_responses": 0,
    }


def test_cache_lifetime_completeness_is_independent_for_each_bucket_and_session(tmp_path):
    write_session(tmp_path, [response("known")], "known.jsonl")
    write_session(tmp_path, [response("partial", usage={
        **BASE_USAGE, "cache_creation": {"ephemeral_5m_input_tokens": 0},
    })], "partial.jsonl")
    write_session(tmp_path, [response("malformed", usage={
        **BASE_USAGE, "cache_creation": [],
    })], "malformed.jsonl")
    trace = ClaudeAdapter().parse_trace(tmp_path)
    sessions = {session["file"]: session for session in trace["sessions"]}
    assert sessions["known.jsonl"]["usage"]["cache_creation"] == CACHE_USAGE
    assert sessions["partial.jsonl"]["usage"]["cache_creation"] == {
        "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": None,
    }
    assert sessions["malformed.jsonl"]["usage"]["cache_creation"] == dict.fromkeys(CACHE_USAGE, None)
    assert trace["totals"]["usage"]["cache_creation"] == dict.fromkeys(CACHE_USAGE, None)
    assert trace["totals"]["usage"]["cache_creation_input_tokens"] == 90
    assert trace["totals"]["incomplete_api_responses"] == 0


def test_response_eligibility_and_completion_are_distinct_from_zero_usage(tmp_path):
    synthetic = response("error", isApiErrorMessage=True)
    synthetic["message"]["model"] = "<synthetic>"
    user = response("user")
    user["type"] = user["message"]["role"] = "user"
    no_usage = response("no-usage")
    del no_usage["message"]["usage"]
    malformed = response("malformed")
    malformed["message"]["usage"] = []
    zero = dict.fromkeys(BASE_USAGE, 0)
    write_session(tmp_path, [
        synthetic, user, no_usage, malformed,
        response("provider/native-response", usage=zero),
        response("empty-usage", usage={}),
        response("no-stop", usage=zero, stop_reason=None),
        response("invalid-stop", usage=zero, stop_reason=123),
        response("missing-counter", usage={key: 0 for key in zero if key != "cache_read_input_tokens"}),
    ])
    totals = ClaudeAdapter().parse_trace(tmp_path)["totals"]
    assert totals["api_responses"] == 5
    assert totals["incomplete_api_responses"] == 4
    assert {key: totals["usage"][key] for key in BASE_USAGE} == zero


def test_empty_malformed_and_unreadable_files_are_tolerated(tmp_path, monkeypatch):
    adapter = ClaudeAdapter()
    empty = adapter.parse_trace(tmp_path)
    assert empty["sessions"] == []
    assert accounting(empty["totals"]) == {
        "api_responses": 0,
        "incomplete_api_responses": 0,
        "usage": {**dict.fromkeys(BASE_USAGE, 0), "cache_creation": dict.fromkeys(CACHE_USAGE, 0)},
    }
    write_session(tmp_path, [], "empty.jsonl")
    broken = write_session(tmp_path, [None, [], 1], "broken.jsonl")
    with broken.open("a") as stream:
        stream.write("{truncated\n")
    unreadable = write_session(tmp_path, [response("unreadable")], "unreadable.jsonl")
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == unreadable:
            raise OSError("unreadable fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert adapter.parse_trace(tmp_path) == empty
    path = write_session(tmp_path, [response(None), response(None)])
    path.write_text("not-json\n" + path.read_text() + '{"message":')
    trace = adapter.parse_trace(tmp_path)
    assert trace["totals"]["api_responses"] == 2
    assert trace["sessions"][0]["record_count"] == 2
