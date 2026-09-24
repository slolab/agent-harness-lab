"""Run inside the Claude image with an AHL-generated managed-settings mount.

A local Anthropic-compatible fixture forces tool calls even when absent from the
model's tool catalog. No live account or provider credential is used.
"""

import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

policy = json.loads(Path("/etc/claude-code/managed-settings.json").read_text())[
    "permissions"
]["deny"]
requests = []


class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "count_tokens" in self.path:
            payload = {"input_tokens": 10}
        else:
            requests.append(data)
            tool_results = [
                b
                for m in data.get("messages", [])
                if isinstance(m.get("content"), list)
                for b in m["content"]
                if b.get("type") == "tool_result"
            ]
            blocks = (
                [
                    {
                        "type": "tool_use",
                        "id": f"tool_{i}",
                        "name": tool,
                        "input": {"url": "https://example.com", "prompt": "Read title"}
                        if tool == "WebFetch"
                        else {"query": "AHL permission fixture"},
                    }
                    for i, tool in enumerate(policy)
                ]
                if policy and not tool_results
                else [{"type": "text", "text": "PERMISSION_FIXTURE_OK"}]
            )
            payload = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": "fixture",
                "content": blocks,
                "stop_reason": "tool_use"
                if blocks[0]["type"] == "tool_use"
                else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        self.send_response(200)
        if data.get("stream") and "content" in payload:
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def emit(kind, value):
                self.wfile.write(
                    f"event: {kind}\ndata: {json.dumps({'type': kind, **value})}\n\n".encode()
                )
                self.wfile.flush()

            emit(
                "message_start",
                {"message": {**payload, "content": [], "stop_reason": None}},
            )
            for i, block in enumerate(payload["content"]):
                initial = {
                    **block,
                    **({"input": {}} if block["type"] == "tool_use" else {"text": ""}),
                }
                emit("content_block_start", {"index": i, "content_block": initial})
                delta = (
                    {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    }
                    if block["type"] == "tool_use"
                    else {"type": "text_delta", "text": block["text"]}
                )
                emit("content_block_delta", {"index": i, "delta": delta})
                emit("content_block_stop", {"index": i})
            emit(
                "message_delta",
                {
                    "delta": {
                        "stop_reason": payload["stop_reason"],
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": 10},
                },
            )
            emit("message_stop", {})
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())


server = ThreadingHTTPServer(("127.0.0.1", 0), API)
threading.Thread(target=server.serve_forever, daemon=True).start()
workspace_settings = Path("/workspace/.claude/settings.json")
workspace_settings.parent.mkdir(parents=True, exist_ok=True)
workspace_settings.write_text(
    json.dumps({"permissions": {"allow": ["WebFetch", "WebSearch"]}})
)
env = {
    **os.environ,
    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}
# Exercise the account-token and gateway-auth initialization paths with synthetic tokens.
if os.environ["FIXTURE_AUTH"] == "account":
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "fixture-account-token"
else:
    env["ANTHROPIC_AUTH_TOKEN"] = "fixture-gateway-token"
    env["ANTHROPIC_API_KEY"] = ""
try:
    result = subprocess.run(
        [
            "claude",
            "-p",
            "Permission fixture",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "3",
            "--model",
            "fixture",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert requests, "native client never reached the local fixture"
    catalog = {t["name"] for t in requests[0]["tools"]}
    for name in ["WebFetch", "WebSearch"]:
        assert (name in catalog) == (name not in policy), (name, catalog, policy)
    results = [
        b
        for r in requests
        for m in r.get("messages", [])
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if b.get("type") == "tool_result"
    ]
    for i, name in enumerate(policy):
        matching = [b for b in results if b["tool_use_id"] == f"tool_{i}"]
        assert matching and all(b.get("is_error") for b in matching), (name, results)
        assert any(
            "No such tool" in str(b["content"]) or "denied" in str(b["content"]).lower()
            for b in matching
        ), matching
    print(
        json.dumps(
            {
                "auth": os.environ["FIXTURE_AUTH"],
                "deny": policy,
                "catalog_web_tools": sorted(catalog & {"WebFetch", "WebSearch"}),
                "forced_calls_rejected": len(policy),
                "result": "passed",
            }
        )
    )
finally:
    server.shutdown()
