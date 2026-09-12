#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const encoded = process.argv[2];
if (!encoded || !/^[A-Za-z0-9+/=]+$/.test(encoded)) {
  process.stderr.write("invalid Orca argv payload\n");
  process.exit(64);
}

let argv;
try {
  argv = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
} catch {
  process.stderr.write("invalid Orca argv payload\n");
  process.exit(64);
}
if (!Array.isArray(argv) || argv.length < 2 || argv.some(
  (value) => typeof value !== "string" || value.includes("\0")
)) {
  process.stderr.write("invalid Orca argument vector\n");
  process.exit(64);
}

const child = spawnSync(argv[0], argv.slice(1), {
  shell: false,
  windowsHide: true,
  encoding: null,
  maxBuffer: 2 * 1024 * 1024,
});
if (child.stdout) process.stdout.write(child.stdout);
if (child.stderr) process.stderr.write(child.stderr);
if (child.error) {
  process.stderr.write(`${child.error.name}:${child.error.code ?? "unknown"}:${child.error.message}\n`);
  process.exit(70);
}
process.exit(Number.isInteger(child.status) ? child.status : 70);
