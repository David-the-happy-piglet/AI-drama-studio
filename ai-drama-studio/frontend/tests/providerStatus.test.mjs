import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Exercise the production helper without adding a browser or another test framework.
const source = readFileSync(new URL("../src/providerStatus.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { getProviderStatuses } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const providers = ["kling", "seedance", "minimax", "mock"].map(provider => ({
  provider, configured: true, configuration_issues: [],
}));

test("all three real platforms appear; mock is excluded", () => {
  const rows = getProviderStatuses(providers, { authenticated: false }, []);
  assert.deepEqual(rows.map(row => row.id), ["kling", "seedance", "minimax"]);
  assert.equal(rows[0].status, "已配置");
  assert.equal(rows[1].status, "API 已配置 · 鉴权未通过");
  assert.equal(rows[2].status, "已配置");
  assert.ok(rows.every(row => row.evidence.includes("不能据此确认")));
  assert.match(rows[2].note, /剩余额度未核验/);
});

test("loading and missing API data are not reported as unconfigured", () => {
  assert.ok(getProviderStatuses([], null, [], true).every(row => row.status === "读取中"));
  assert.ok(getProviderStatuses([], null, []).every(row => row.status === "状态未知"));
});

test("configuration problems are visible without using login state as evidence", () => {
  const rows = getProviderStatuses([{ provider: "kling", configured: false, configuration_issues: ["missing key"] }], { authenticated: true }, []);
  assert.equal(rows[0].status, "待配置");
  assert.equal(rows[0].note, "missing key");
  assert.equal(rows[1].status, "状态未知");
});

test("Seedance authentication failure does not hide Kling or MiniMax", () => {
  const rows = getProviderStatuses(providers, { authenticated: false, error: "query failed" }, []);
  assert.equal(rows[0].status, "已配置");
  assert.equal(rows[1].status, "API 已配置 · 鉴权未确认");
  assert.equal(rows[1].note, "query failed");
  assert.equal(rows[2].status, "已配置");
});

test("authentication success remains distinct from successful video evidence", () => {
  const rows = getProviderStatuses(providers, { authenticated: true }, []);
  assert.equal(rows[1].status, "API 已配置 · 鉴权通过");
  assert.match(rows[1].evidence, /未见成功/);
});

test("only provider-specific completed videos with task and output count", () => {
  const video = { provider: "kling", status: "succeeded", external_id: "task", output_path: "https://example.invalid/video.mp4" };
  const rows = getProviderStatuses(providers, null, [
    video, { ...video, status: "completed" }, { ...video, status: "failed" },
    { ...video, external_id: null }, { ...video, output_path: null },
    { ...video, provider: "mock" },
  ]);
  assert.equal(rows[0].evidence, "近期记录中有 2 条成功视频");
  assert.match(rows[1].evidence, /未见成功/);
  assert.match(rows[2].evidence, /未见成功/);
  assert.equal(rows[0].status, "已配置");
});
