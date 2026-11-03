import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/characterBindings.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { bindingLabel, activeElementStatuses } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("local-only, ready and outdated references are distinct", () => {
  assert.equal(bindingLabel({ version: 1 }), "未创建可灵主体");
  assert.equal(bindingLabel({ version: 1 }, { source_version: 1, status: "succeeded" }), "已绑定");
  assert.match(bindingLabel({ version: 2 }, { source_version: 1, status: "succeeded" }), /重新绑定/);
});

test("uncertain submission is not treated as safe to retry", () => {
  assert.ok(activeElementStatuses.includes("unknown"));
  assert.ok(activeElementStatuses.includes("submitting"));
  assert.ok(!activeElementStatuses.includes("failed"));
});
