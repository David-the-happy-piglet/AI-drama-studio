import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/shotCharacters.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { characterSelectionPayload } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("adding characters preserves existing outfits and uses the current version", () => {
  const shot = { version: 7, characters: [{ character_id: "shen", outfit_id: "home" }] };
  const result = characterSelectionPayload(shot, ["shen", "new", "new"]);
  assert.deepEqual(result, { expected_version: 7, characters: [{ character_id: "shen", outfit_id: "home" }, { character_id: "new" }] });
  assert.notEqual(result.characters[0], shot.characters[0]);
  assert.equal(shot.characters.length, 1);
});

test("removing all visual characters explicitly persists an empty list", () => {
  assert.deepEqual(characterSelectionPayload({ version: 2, characters: [{ character_id: "shen" }] }, []), { expected_version: 2, characters: [] });
});

test("patch only changes associations, never prompt, generation mode or identity assets", () => {
  const result = characterSelectionPayload({ version: 1, characters: [], generation_prompt: "keep", generation_mode: "image_to_video" }, ["shen"]);
  assert.deepEqual(Object.keys(result).sort(), ["characters", "expected_version"]);
});
