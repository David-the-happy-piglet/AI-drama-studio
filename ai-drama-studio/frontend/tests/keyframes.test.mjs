import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = readFileSync(new URL("../src/keyframes.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { keyframePending, keyframePollable, keyframePrompt, selectedKeyframeToken } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("reference prompt matches actual submission; text mode never claims image input", () => {
  assert.equal(keyframePrompt("<<<image_1>>> identity", " shot ", "bad", true), "<<<image_1>>> identity\nshot\n避免：bad");
  assert.equal(keyframePrompt("identity", " shot ", "bad", false), "shot");
});
test("uncertain submissions block duplicates but never auto resubmit", () => {
  assert.equal(keyframePending({status:"unknown"}), true);
  assert.equal(keyframePollable({status:"unknown"}), false);
  assert.equal(keyframePollable({status:"succeeded",candidates:[{path:null}]}), true);
  assert.equal(keyframePollable({status:"download_failed"}), false);
});
test("only usable selected images in frame modes attach; uploads explicitly override", () => {
  const selected = {usable:true, selection_token:"frame-1"};
  assert.equal(selectedKeyframeToken(false, selected, "image_to_video"), "frame-1");
  assert.equal(selectedKeyframeToken(false, selected, "first_last_frame"), "frame-1");
  assert.equal(selectedKeyframeToken(true, selected, "image_to_video"), null);
  assert.equal(selectedKeyframeToken(false, {...selected,usable:false}, "image_to_video"), null);
  assert.equal(selectedKeyframeToken(false, selected, "text_to_video"), null);
  assert.equal(selectedKeyframeToken(false, selected, "reference_to_video"), null);
});
