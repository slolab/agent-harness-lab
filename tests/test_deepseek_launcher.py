"""Exercise the real Node launcher with a local TCP child, without Docker/API keys."""

import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "docker/deepseek/ahl-deepseek.cjs"


@pytest.fixture
def launcher(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for DeepSeek launcher process tests")
    child = tmp_path / "dsh"
    child.write_text(
        f"#!{node}\n"
        + """
const net = require('node:net');
const args = process.argv.slice(2);
if (JSON.stringify(args) !== JSON.stringify(['--profile','web','--host','127.0.0.1','--port',args[5],'--no-open'])) process.exit(80);
if (process.env.CHILD_EXIT) process.exit(Number(process.env.CHILD_EXIT));
const server = net.createServer({allowHalfOpen: true}, s => {
  if (!process.env.CHILD_REPLY_AFTER_EOF) { s.pipe(s); return; }
  const parts = [];
  s.on('data', part => parts.push(part));
  s.on('end', () => setTimeout(() => s.end(Buffer.concat(parts)), 20));
});
server.on('error', () => process.exit(19));
server.listen(Number(args[5]), '127.0.0.1', () => console.log('READY'));
process.on('SIGTERM', () => process.exit(23));
process.on('SIGINT', () => process.exit(24));
"""
    )
    child.chmod(0o755)
    processes = []

    def start(port, **extra):
        proc = subprocess.Popen(
            [node, str(LAUNCHER), "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={
                "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                **extra,
            },
        )
        processes.append(proc)
        return proc

    yield start
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.communicate(timeout=7)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)
        finally:
            # Failed assertions must not leave the fake native child orphaned.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_relay_bytes_backpressure_disconnect_and_signal_cleanup(launcher):
    for port, relay, sig, code in [
        (free_port(), 3081, signal.SIGTERM, 23),
        (3081, 3082, signal.SIGINT, 24),
    ]:
        proc = launcher(port, CHILD_REPLY_AFTER_EOF="1" if port == 3081 else "")
        ready, _, _ = select.select([proc.stdout], [], [], 10)
        assert ready and proc.stdout.readline().strip() == "READY"
        with socket.create_connection(("127.0.0.1", relay), timeout=5) as sock:
            payload = bytes(range(256)) * 2048
            sock.sendall(payload)
            if port == 3081:
                sock.shutdown(socket.SHUT_WR)
            received = bytearray()
            while len(received) < len(payload):
                part = sock.recv(65536)
                assert part
                received.extend(part)
            assert received == payload
            proc.send_signal(sig)
            assert proc.wait(timeout=5) == code
            assert sock.recv(1) == b""
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", relay))


def test_bind_failures_and_child_exit_do_not_leave_listener(launcher):
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("0.0.0.0", 3081))
        occupied.listen()
        proc = launcher(free_port())
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode != 0 and "EADDRINUSE" in stderr
        assert "READY" not in stdout
    proc = launcher(free_port(), CHILD_EXIT="17")
    proc.communicate(timeout=5)
    assert proc.returncode == 17
    with socket.socket() as target:
        target.bind(("127.0.0.1", 0))
        target.listen()
        proc = launcher(target.getsockname()[1])
        proc.communicate(timeout=5)
        assert proc.returncode == 19
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        check.bind(("127.0.0.1", 3081))
