from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from ahl import keyusage
from ahl.docker import CONTAINER_WORKSPACE, docker_daemon_unreachable
from ahl.harnesses.base import TurnReport, provider_key
from ahl.runs import PreparedRun, start_container, write_native_trace
from ahl.trace import TOKEN_FIELDS, token_totals, write_trace

EXIT_CODES = {"completed": 0, "failed": 1, "error": 3, "timeout": 124, "interrupted": 130}
WARNINGS = {
    "usage_not_updated": "OpenRouter key usage did not rise after the turn",
    "usage_read_failed": "OpenRouter key usage could not be read",
    "trace_unreadable": "the native trace could not be parsed",
}


@dataclass(frozen=True)
class TurnOutcome:
    status: str
    reason: str | None = None
    message: str | None = None


class Interrupts:
    # Ctrl-C raises only inside interruptible(), which wraps the waits on Docker, the harness and the
    # key. Elsewhere it is counted and raised when the next interruptible() starts, so recording,
    # cleanup and result.json always finish.
    def __init__(self) -> None:
        self.count = 0
        self._handled = 0
        self._raising = False

    @contextmanager
    def installed(self) -> Iterator[None]:
        previous = signal.signal(signal.SIGINT, self._handle)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)

    @contextmanager
    def interruptible(self) -> Iterator[None]:
        if self._handled < self.count:
            self._handled = self.count
            raise KeyboardInterrupt
        self._raising = True
        try:
            yield
        finally:
            self._raising = False

    def _handle(self, signum: int, frame: object) -> None:
        self.count += 1
        if self._raising:
            self._handled = self.count
            raise KeyboardInterrupt


def run_headless(
    run: PreparedRun, turn_files: list[Path], timeout: float, failure: str | None, interrupts: Interrupts
) -> int:
    return _HeadlessRun(run, turn_files, timeout, failure, interrupts).execute()


class _HeadlessRun:
    def __init__(
        self, run: PreparedRun, turn_files: list[Path], timeout: float, failure: str | None, interrupts: Interrupts
    ) -> None:
        self.run = run
        self.driver = run.adapter.driver
        self.timeout = timeout
        self.failure = failure
        self.interrupts = interrupts
        self.key = provider_key(run.config) if run.config.provider.name == "openrouter" else None
        self.turns = [self._record(index, path) for index, path in enumerate(turn_files, start=1)]
        self.warnings: list[dict[str, Any]] = []

    def execute(self) -> int:
        interrupted = self.interrupts.count > 0
        if self.failure is None and not interrupted:
            try:
                with self.interrupts.interruptible():
                    self.failure = self._start()
                if self.failure is None:
                    session_id = None
                    for turn in self.turns:
                        session_id = self._turn(turn, session_id) or session_id
                        if turn["status"] != "completed":
                            break
            except KeyboardInterrupt:
                interrupted = True
            self._teardown()
        status, reason = self._settle_statuses(interrupted)
        events = self._write_traces()
        (self.run.dir / "result.json").write_text(json.dumps(self._result(status, reason, events), indent=2))
        code = f" ({reason['code']})" if reason else ""
        typer.echo(f"Result: {status}{code} -> {self.run.dir / 'result.json'}")
        return EXIT_CODES[status]

    def _record(self, index: int, source: Path) -> dict[str, Any]:
        prompt = self.run.dir / "turns" / str(index) / "prompt.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_bytes(source.read_bytes())
        return {
            "index": index,
            "prompt_file": str(prompt.relative_to(self.run.dir)),
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "started_at": None,
            "ended_at": None,
            "wall_clock_seconds": None,
            "exit_code": None,
            "status": None,
            "reason": None,
            "session_id": None,
            "key_usage": None,
        }

    def _start(self) -> str | None:
        try:
            start_container(self.run)
        except FileNotFoundError:
            return "Docker CLI not found on PATH"
        except subprocess.CalledProcessError as exc:
            return f"container start failed: `{' '.join(exc.cmd[:3])}` exited with code {exc.returncode}"
        return None

    def _turn(self, turn: dict[str, Any], session_id: str | None) -> str | None:
        index, directory = turn["index"], self.run.dir / "turns" / str(turn["index"])
        with self.interrupts.interruptible():
            before = keyusage.read(self.key) if self.key else None
        command = [
            "docker", "exec", "-i", "-w", CONTAINER_WORKSPACE, self.run.container,
            *self.driver.command(self.run.config, session_id),
        ]
        started, exit_code, outcome = datetime.now(timezone.utc), None, None
        turn["started_at"] = started.isoformat()
        interrupted = TurnOutcome("interrupted", "interrupted", f"turn {index} was interrupted")
        try:
            with (
                (directory / "prompt.md").open("rb") as stdin,
                (directory / "stdout.jsonl").open("wb") as stdout,
                (directory / "stderr.log").open("wb") as stderr,
                self.interrupts.interruptible(),
            ):
                exit_code = subprocess.run(
                    command, stdin=stdin, stdout=stdout, stderr=stderr, timeout=self.timeout, check=False
                ).returncode
        except subprocess.TimeoutExpired:
            outcome = TurnOutcome("timeout", "timeout", f"turn {index} exceeded --timeout {self.timeout:g} seconds")
        except KeyboardInterrupt:
            outcome = interrupted
        ended = datetime.now(timezone.utc)
        if outcome is not None:
            self._in_container("kill -KILL -1")
        report = self.driver.report((directory / "stdout.jsonl").read_text(errors="replace"))
        if outcome is None:
            outcome = self._classify(exit_code, report, (directory / "stderr.log").read_text(errors="replace"))
        if self.key:
            measured = keyusage.after_turn(self.key, before, self.interrupts.interruptible)
            if measured.interrupted and outcome.status not in ("timeout", "interrupted"):
                outcome = interrupted
            turn["key_usage"] = measured.key_usage
            if measured.warning:
                self.warnings.append({"code": measured.warning, "turn": index, "message": WARNINGS[measured.warning]})
        turn.update(
            ended_at=ended.isoformat(),
            wall_clock_seconds=round((ended - started).total_seconds(), 3),
            exit_code=exit_code,
            status=outcome.status,
            reason={"code": outcome.reason, "message": outcome.message} if outcome.reason else None,
            session_id=report.session_id,
        )
        typer.echo(f"Turn {index}: {outcome.status}{f' ({outcome.reason})' if outcome.reason else ''}")
        return turn["session_id"]

    def _classify(self, exit_code: int, report: TurnReport, stderr: str) -> TurnOutcome:
        daemon = "error response from daemon" in stderr.lower() or docker_daemon_unreachable(stderr)
        if exit_code in (125, 126, 127) or daemon or (exit_code and not self._running()):
            detail = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")[:300]
            return TurnOutcome("error", "infra", f"docker exec exited with code {exit_code}" + (f": {detail}" if detail else ""))
        harness = self.run.config.harness.name
        provider = "provider_error" if report.provider_error else None
        if exit_code:
            detail = f": {report.error}" if report.error else ""
            return TurnOutcome("failed", provider or "harness_exit", f"{harness} exited with code {exit_code}{detail}")
        if report.error is not None:
            return TurnOutcome("failed", provider or "harness_reported_error", report.error)
        if not report.replied:
            return TurnOutcome("failed", "no_assistant_output", f"{harness} produced no assistant message")
        return TurnOutcome("completed")

    def _running(self) -> bool:
        result = _docker("container", "inspect", "--format", "{{.State.Running}}", self.run.container)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _in_container(self, script: str) -> bool:
        return _docker("exec", self.run.container, "sh", "-c", script).returncode == 0

    def _teardown(self) -> None:
        owner = f"{os.getuid()}:{os.getgid()}"
        paths = shlex.join(self.run.writable_paths)
        # kill -1 spares only PID 1 and the caller, so no harness process writes after the chown.
        if not self._in_container(f"kill -KILL -1 2>/dev/null; chown -R -h {owner} {paths} 2>/dev/null; exit 0"):
            typer.echo(
                f"[ahl] warning: container {self.run.container} is gone; files it wrote under {self.run.dir} "
                "may belong to root",
                err=True,
            )
        _docker("rm", "-f", self.run.container)

    def _settle_statuses(self, interrupted: bool) -> tuple[str, dict[str, str] | None]:
        pending = [turn for turn in self.turns if turn["status"] is None]
        if interrupted and pending:
            first = pending.pop(0)
            first.update(status="interrupted", reason={"code": "interrupted", "message": f"turn {first['index']} was interrupted"})
        stopped = next((turn for turn in self.turns if turn["status"] not in (None, "completed")), None)
        cause = f"turn {stopped['index']} {stopped['status']}" if stopped else "the run failed before turn 1"
        for turn in pending:
            turn.update(status="skipped", reason={"code": "skipped_after_failure", "message": cause})
        if stopped:
            return stopped["status"], stopped["reason"]
        if self.failure is not None:
            return "error", {"code": "infra", "message": self.failure}
        return "completed", None

    def _write_traces(self) -> list[dict[str, Any]] | None:
        # A parser that fails on partial native state must not cost the caller result.json.
        try:
            write_native_trace(self.run)
            return write_trace(self.run.dir, self.driver.trace(self.run.dir), self.turns)
        except Exception as exc:
            message = f"{WARNINGS['trace_unreadable']}: {exc}"
            typer.echo(f"[ahl] warning: {message}", err=True)
            self.warnings.append({"code": "trace_unreadable", "turn": None, "message": message})
            write_trace(self.run.dir, [], self.turns)
            return None

    def _result(
        self, status: str, reason: dict[str, str] | None, events: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        config = self.run.config
        started = [turn for turn in self.turns if turn["started_at"]]
        deltas = [(turn["key_usage"] or {}).get("delta_usd") for turn in started]
        sessions = [turn["session_id"] for turn in self.turns if turn["session_id"]]
        return {
            "run_id": self.run.dir.name,
            "harness": config.harness.name,
            "provider": config.provider.name,
            "model": config.model.name or None,
            "status": status,
            "reason": reason,
            "session_id": sessions[-1] if sessions else None,
            "turns": self.turns,
            "totals": {
                "wall_clock_seconds": round(sum(turn["wall_clock_seconds"] or 0 for turn in started), 3),
                "cost_usd_key_delta": round(sum(deltas), 10) if self.key and None not in deltas else None,
                **(token_totals(events) if events is not None else dict.fromkeys(TOKEN_FIELDS)),
            },
            "warnings": self.warnings,
        }


def _docker(*args: str) -> subprocess.CompletedProcess:
    # Its own session keeps the terminal's Ctrl-C from killing a cleanup command halfway.
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False, start_new_session=True)
