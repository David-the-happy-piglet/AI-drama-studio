# AI Drama Studio architecture

## 1. Scope and quality boundary

The system treats an episode as a persistent graph of independently editable
shots, never as one video-model prompt. Phase 1 establishes the system of record
and provider boundary; later phases add automation without changing those core
contracts.

```text
Project
├── reusable Character identity
│   └── independently versioned Outfit
├── Script (source idea + structured JSON)
└── Episode
    └── ordered Scene
        └── ordered Shot
            └── immutable-attempt Generation history
```

A failed generation affects one shot attempt. The accepted result, retry policy,
QA result, continuity state, and final timeline remain separate concerns.

## 2. Runtime components

| Component | Responsibility | Phase 1 state |
| --- | --- | --- |
| FastAPI application | CRUD, validation, OpenAPI, manual generation controls | Implemented |
| PostgreSQL / SQLite | Canonical domain state and generation history | Implemented |
| Local/S3-compatible storage | References, keyframes, videos, extracted frames | Local directories reserved; S3 env reserved |
| VideoProvider registry | Stable business-facing video interface | Implemented |
| Seedance adapter | Maps provider-neutral requests to the official local `dreamina` CLI | Implemented |
| Kling adapter | Contains volatile HTTP auth, endpoints, payloads, responses | Fail-closed until verified config |
| Redis / worker | Durable long-running generation jobs | Infrastructure reserved; worker is Phase 5 |
| Next.js UI | Human-in-the-loop review and editing | Boundary reserved |

In production, generation submission and polling should move from the API process
to idempotent worker jobs. The database contract does not need to change: the
worker claims a `Generation`, sends its saved request snapshot, and updates that
single attempt.

## 3. Core data model

### Character and Outfit

`Character` stores immutable identity-oriented traits: face, body, hair, eyes,
distinctive features, voice binding, visual style, and negative constraints.
`Outfit` is a child entity with its own ID, references, version, palette, and
constraints. A shot binds `{character_id, outfit_id}`; changing clothes never
redefines identity.

The Phase 2 reference library fits underneath these records:

```text
data/characters/{character_id}/
├── character.json
├── face/{front,left_45,right_45,profile_left,profile_right}.png
├── body/{full_front,full_side,full_back}.png
├── expressions/
├── outfits/{outfit_id}/
├── references/
└── generated/
```

### Script, Episode, Scene, Shot

`Script.structured_content` preserves the complete LLM-produced JSON and a
version. Episodes, scenes, and shots are normalized so operators can edit or
regenerate a single unit. `Shot` remains provider-neutral and stores composition,
action, emotion, camera intent, continuity/reference requirements, generation
mode, and optional manual provider override.

### Generation

Every attempt is append-only from the operator's point of view. It denormalizes
the project/episode/scene/shot IDs and records provider, model, prompt, negative
prompt, references, seed, duration, resolution, aspect ratio, API parameters,
request snapshot, external task ID, timestamps, cost, retry count, QA result,
provider response, output path, status, and error. This supports reproduction, debugging,
benchmarking, and cost attribution without relying on the current shot version.

## 4. Provider contract

`VideoProvider` exposes:

- `generate(request)`
- `get_status(external_id)`
- `cancel(external_id)`
- `download_result(external_id, destination)`
- `estimate_cost(request)`
- `capabilities()`

The internal `VideoGenerationRequest` is stable and model-agnostic. Seedance uses
the official local `dreamina` executable and its OAuth device-flow session. The
adapter selects a CLI subcommand from `generation_mode`, invokes it without a
shell, saves the exact arguments and CLI version, persists `submit_id`, and uses
`query_result` for later status refreshes. Model compatibility remains owned by
the installed CLI and is not duplicated in business code.

Kling remains a fail-closed HTTP adapter. Its YAML maps internal field names to
provider fields and response paths; checked-in configuration intentionally makes
no unverified endpoint, model, authentication, payload, or capability claim.

## 5. Planned extension sequence

1. Phase 2: reference libraries, locations, keyframe approval, prompt compilers,
   rule-based model router.
2. Phase 3: continuity snapshots, previous-frame selection, CV/VLM QA, targeted
   retry, cost tracking.
3. Phase 4: benchmarks, TTS, subtitles, FFmpeg assembly, JSON timeline.
4. Phase 5: durable agent/worker orchestration for a full episode, while keeping
   explicit human approval gates before expensive generation and final assembly.

The most important invariant is retained across all phases: automation may add or
update a shot attempt, but it cannot silently replace human-approved identity,
outfit, scene, keyframe, or video state.
