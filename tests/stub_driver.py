import os

from ahl import cli
from ahl.harnesses import get_adapter
from ahl.harnesses.base import TurnOutcome


class StubDriver:
    def applied_model_parameters(self, config):
        return frozenset()

    def env(self, config):
        return {}

    def command(self, config, session_id):
        return ["sh", "-c", os.environ["AHL_STUB_TURN"]]

    def session_id(self, stdout):
        return None

    def outcome(self, exit_code, stdout):
        if exit_code:
            return TurnOutcome("failed", "harness_exit", f"stub exited with code {exit_code}")
        return TurnOutcome("completed")

    def trace(self, run_dir):
        return []


get_adapter("opencode").driver = StubDriver()
cli.app(prog_name="ahl")
