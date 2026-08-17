#!/usr/bin/env bun
/**
 * Phase 10 Plan 08 node relay forwarder for the OMP sidecar.
 * Buffers client data until the relay UDS connects.
 */

import { createServer, connect } from "node:net";

const LISTEN_HOST = "127.0.0.1";
const LISTEN_PORT = 11434;
const UDS_PATH = "/run/prime-relay/relay.sock";
const ALLOWED_HOSTNAME = "127.0.0.1";
const ALLOWED_PORT = 11434;

function frame(hostname, port) {
  const host = Buffer.from(hostname, "ascii");
  const out = Buffer.alloc(2 + host.length + 2);
  out.writeUInt16BE(host.length, 0);
  host.copy(out, 2);
  out.writeUInt16BE(port, 2 + host.length);
  return out;
}

const server = createServer((client) => {
  let relay;
  const pending = [];
  let relayReady = false;
  try {
    relay = connect(UDS_PATH);
  } catch (err) {
    client.destroy();
    return;
  }
  // Attach client data handler immediately; buffer until relay is ready.
  client.on("data", (d) => {
    if (relayReady) relay.write(d);
    else pending.push(d);
  });
  client.on("error", () => relay.destroy());
  client.on("close", () => relay.destroy());

  relay.on("connect", () => {
    relay.write(frame(ALLOWED_HOSTNAME, ALLOWED_PORT));
    relayReady = true;
    for (const d of pending) relay.write(d);
    pending.length = 0;
    relay.on("data", (d) => client.write(d));
    relay.on("end", () => client.end());
    relay.on("error", () => client.destroy());
  });
  relay.on("error", () => client.destroy());
});

server.listen(LISTEN_PORT, LISTEN_HOST, () => {
  console.error(JSON.stringify({ status: "listening", host: LISTEN_HOST, port: LISTEN_PORT }));
});
