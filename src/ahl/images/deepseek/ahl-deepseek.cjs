#!/usr/bin/env node
'use strict';
// DSH requires a loopback listener. Publish this raw TCP relay through Docker's
// host-loopback mapping so its native token URL, cookies and WebSockets survive.
const net = require('node:net');
const { spawn } = require('node:child_process');
const { constants } = require('node:os');

const args = process.argv.slice(2);
const port = args.length === 0 ? 3080 : Number(args[1]);
if ((args.length !== 0 && (args.length !== 2 || args[0] !== '--port')) ||
    !Number.isInteger(port) || port < 1 || port > 65535) {
  console.error('Usage: ahl-deepseek [--port 1..65535]');
  process.exit(2);
}
const relayPort = port === 3081 ? 3082 : 3081;
const sockets = new Set();
let child;
let stopping = false;
let killTimer;
const server = net.createServer({ allowHalfOpen: true }, (client) => {
  const upstream = net.connect({ host: '127.0.0.1', port, allowHalfOpen: true });
  for (const socket of [client, upstream]) {
    sockets.add(socket);
    socket.on('error', () => { client.destroy(); upstream.destroy(); });
    socket.on('close', () => sockets.delete(socket));
  }
  // Each readable EOF ends only the peer's writable side. Let queued writes
  // drain: destroying the peer on a normal close truncates half-closed replies.
  // pipe handles backpressure on both streams; bytes and headers are untouched.
  client.pipe(upstream).pipe(client);
});
function closeRelay() {
  stopping = true;
  server.close();
  for (const socket of sockets) socket.destroy();
}
function finish(code) {
  clearTimeout(killTimer);
  closeRelay();
  process.exitCode = code;
}
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    closeRelay();
    if (child) {
      child.kill(signal);
      // A stuck child must not keep the launcher or published listener alive.
      killTimer ??= setTimeout(() => child.kill('SIGKILL'), 5000);
    } else {
      process.exitCode = 128 + constants.signals[signal];
    }
  });
}
server.on('error', (error) => {
  console.error(`ahl-deepseek relay: ${error.message}`);
  closeRelay();
  if (child) child.kill('SIGTERM');
  process.exitCode = 1;
});
server.listen(relayPort, '0.0.0.0', () => {
  if (stopping) return;
  child = spawn('dsh', ['--profile', 'web', '--host', '127.0.0.1', '--port', String(port), '--no-open'], { stdio: 'inherit' });
  child.on('error', (error) => {
    console.error(`ahl-deepseek: ${error.message}`);
    finish(1);
  });
  child.on('exit', (code, signal) => finish(code ?? 128 + (constants.signals[signal] ?? 1)));
});
