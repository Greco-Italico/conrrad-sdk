# Truth Hierarchy v0

| Field | Value |
|-------|--------|
| Version | `TRUTH_HIERARCHY_V0` |
| Scope | Constitutional conflict resolution |
| Companion | `CONRRAD_DOCTRINE_CONTRACT.md` |

## Purpose

Define a stable epistemic order so all modules resolve contradictions the same way.

## Hierarchy (highest to lowest)

1. RuntimeBlock chain verification (`/api/runtime/blocks/verify`)
2. Replay reconstruction (`/api/runtime/blocks/replay`)
3. Mutation authority trail (`/api/mutations/orchestrate`, `MUTATION_*`)
4. Timeline causal events (`CONTINUITY_*`, `RUNTIME_BLOCK_SEALED`)
5. Physiology snapshots (`physiology_snapshots.jsonl`)
6. Cached UI state (dashboard/local cache)
7. Inferred narrative (human/LLM prose)

## Constitutional rules

- Lower-priority sources MUST conform to higher-priority sources.
- Narrative MUST degrade toward evidence.
- Narrative MUST NOT exceed replay certainty.
- If `verify` and `replay` disagree, treat as `INTEGRITY_GAP` candidate.

## Conflict shortcuts

| Conflict | Winner | Required output |
|----------|--------|-----------------|
| replay vs UI | replay | `FREEZE` or `REQUIRE_HUMAN` |
| lineage vs cached mission | lineage | rehydrate + `SOFT_DENY` |
| confidence high + replay broken | replay | confidence collapse + `FREEZE` |
| restore rich + missing checkpoint | checkpoint reality | `REQUIRE_HUMAN` |

## Operational question

Before accepting any claim:

```text
Can this be derived from verify + replay?
```

If no, it is advisory, not truth.

