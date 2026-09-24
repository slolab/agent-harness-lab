import os

from ahl import cli
from ahl.harnesses import get_adapter
from ahl.harnesses.base import TurnReport


class StubDriver:
    def applied_model_parameters(self, config):
        return frozenset()

    def env(self, config):
        return {}

    def command(self, config, session_id):
        return ["sh", "-c", os.environ["AHL_STUB_TURN"]]

    def report(self, stdout):
        return TurnReport(session_id=None, error=None, provider_error=False, replied=True)

    def trace(self, run_dir):
        return []


get_adapter("opencode").driver = StubDriver()
cli.app(prog_name="ahl")
