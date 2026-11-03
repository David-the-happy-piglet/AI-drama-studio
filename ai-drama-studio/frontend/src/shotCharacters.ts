import type { Shot, ShotCharacter } from "./types";

// Retained characters keep their per-shot outfit; new characters have no inferred outfit.
export function characterSelectionPayload(shot: Pick<Shot, "version" | "characters">, ids: string[]) {
  const existing = new Map(shot.characters.map(item => [item.character_id, item]));
  const characters: ShotCharacter[] = [...new Set(ids)].map(character_id => {
    const previous = existing.get(character_id);
    return previous ? { ...previous } : { character_id };
  });
  return { expected_version: shot.version, characters };
}
