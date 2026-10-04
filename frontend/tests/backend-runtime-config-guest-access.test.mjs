import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const repoRoot = path.resolve(import.meta.dirname, "..", "..");

async function workflowText(...segments) {
  return readFile(path.join(repoRoot, ...segments), "utf8");
}

test("cloud backend runtime workflows disable retired membership-header authentication", async () => {
  const [aiRuntime, phase7Runtime, phase10Https] = await Promise.all([
    workflowText(".github", "workflows", "backend-ai-runtime-config.yml"),
    workflowText(".github", "workflows", "phase7-backend-runtime-config.yml"),
    workflowText(".github", "workflows", "phase10-https-custom-domain.yml"),
  ]);

  assert.match(aiRuntime, /DEV_ALLOW_MEMBERSHIP_HEADER="0"/);
  assert.match(phase7Runtime, /"DEV_ALLOW_MEMBERSHIP_HEADER", "value": "0"/);
  assert.match(phase10Https, /"DEV_ALLOW_MEMBERSHIP_HEADER", "value": "0"/);
});
