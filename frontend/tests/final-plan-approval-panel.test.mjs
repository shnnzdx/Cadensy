import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("final plan panel states the approval and closed-invite copy", async () => {
  const source = await readFile(
    new URL("../../trip/src/final/plan-feature/FinalPlanApprovalPanel.jsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /You approved this itinerary/);
  assert.match(source, /The invite link is now closed/);
  assert.match(source, /The itinerary changed since you last looked — please review and approve again/);
  assert.match(source, /approved an earlier version — needs re-approval/);
  assert.match(source, /Final plan approved by all participants on/);
  assert.match(source, /The invite remains closed/);
  assert.match(source, /disabled=\{!ready \|\| busy\}/);
});
