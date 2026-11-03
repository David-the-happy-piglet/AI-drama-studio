import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/portraitJobs.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { portraitPending, portraitNeedsPolling, mergePortraitJob, portraitInputLabel, referenceEffectivePrompt, referencePresets } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const job = (status, candidates = []) => ({ id: "one", created_at: "2026-09-06T00:00:00Z", status, candidates });

test("legacy text jobs can never be labelled as reference-image generation", () => {
  assert.match(portraitInputLabel({}), /未使用母版图片/);
  assert.match(portraitInputLabel({ mode: "text" }), /未使用母版图片/);
  assert.match(portraitInputLabel({ mode: "reference" }), /基于母版参考图/);
});

test("reference prompt preview includes image identity reference and negative requirements", () => {
  assert.equal(referenceEffectivePrompt("参考 <<<image_1>>>", " 侧面 ", "模糊"), "参考 <<<image_1>>>\n侧面\n避免：模糊");
  assert.equal(referenceEffectivePrompt("参考 <<<image_1>>>", "侧面", ""), "参考 <<<image_1>>>\n侧面");
  assert.match(referencePresets.profile, /90度/);
  assert.match(referencePresets.three_quarter, /45度/);
  assert.ok(Object.values(referencePresets).every(prompt => prompt.includes("单人单幅")));
});

test("uncertain tasks block another paid generation but require manual recovery", () => {
  assert.equal(portraitPending(job("unknown")), true);
  assert.equal(portraitNeedsPolling(job("unknown")), false);
  for (const status of ["submitting", "submitted", "processing"]) {
    assert.equal(portraitPending(job(status)), true);
    assert.equal(portraitNeedsPolling(job(status)), true);
  }
});

test("completed responses with undownloaded images are polled; partial failures are manual", () => {
  assert.equal(portraitNeedsPolling(job("succeeded", [{ path: null }])), true);
  assert.equal(portraitNeedsPolling(job("succeeded", [{ path: "assets/face.png" }])), false);
  for (const status of ["failed", "partial", "download_failed"]) {
    assert.equal(portraitPending(job(status)), false);
    assert.equal(portraitNeedsPolling(job(status)), false);
  }
});

test("status updates replace only their own batch, newest first", () => {
  const newer = { ...job("submitted"), id: "two", created_at: "2026-09-06T01:00:00Z" };
  const original = [newer, job("submitted")];
  const updated = mergePortraitJob(original, job("succeeded"));
  assert.deepEqual(updated.map(item => item.id), ["two", "one"]);
  assert.equal(updated[1].status, "succeeded");
  assert.equal(original[1].status, "submitted");
});
