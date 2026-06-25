uv run python <<'PY' > runs/vhmodels-test/conversation-condensed.json
import json, sqlite3
from pathlib import Path

db = Path("runs/vhmodels-test/opencode/data/opencode.db")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
sessions = []
for sid, title in conn.execute("SELECT id, title FROM session"):
    turns = []
    for mid, mdata in conn.execute(
        "SELECT id, data FROM message WHERE session_id=? ORDER BY time_created, id", (sid,)
    ):
        role = json.loads(mdata).get("role")
        for (pdata,) in conn.execute("SELECT data FROM part WHERE message_id=? ORDER BY id", (mid,)):
            p = json.loads(pdata)
            if p.get("type") == "text" and p.get("text"):
                turns.append({"role": role, "type": "text", "text": p["text"]})
            elif p.get("type") == "tool":
                st = p.get("state") or {}
                entry = {"role": role, "type": "tool", "tool": p.get("tool"), "input": st.get("input")}
                if st.get("output") is not None:
                    out = st["output"]
                    if isinstance(out, str) and len(out) > 1000:
                        out = out[:1000] + f"... [{len(st['output'])} chars total]"
                    entry["output"] = out
                if st.get("error"):
                    entry["error"] = st["error"]
                turns.append(entry)
    sessions.append({"id": sid, "title": title, "turns": turns})
print(json.dumps({"run_id": "vhmodels-test", "sessions": sessions}, indent=2, ensure_ascii=False))
PY