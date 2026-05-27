# Policy Output Matrix v0

| Field | Value |
|-------|--------|
| Version | `POLICY_OUTPUT_MATRIX_V0` |
| Source | `policyArbiter` + continuity confidence policy |
| Companion | `CONRRAD_DEGRADATION_SEMANTICS.md` |

## Outputs

| Output | Meaning | Mutation behavior |
|--------|---------|-------------------|
| `ALLOW` | Evidence sufficient | Execute |
| `ALLOW_WITH_CHECKPOINT` | Execute with added safety | Capture checkpoint then execute |
| `SOFT_DENY` | Elevated uncertainty | Deny now, allow retry after rehydrate |
| `REQUIRE_HUMAN` | Ambiguity beyond autonomous confidence | Pause and request operator decision |
| `FREEZE` | Continuity unsafe or policy hard gate | Deny all constitutional mutations |
| `HARD_FREEZE` | Integrity compromised (verify/replay failure) | Deny mutations + incident handling |

## Condition -> output defaults

| Condition | Output |
|-----------|--------|
| verify OK, replay OK, confidence >= 0.62 | `ALLOW` |
| `fragile_continuity` + replay OK | `ALLOW_WITH_CHECKPOINT` |
| stale propagation or minor epoch staleness | `SOFT_DENY` |
| restore uncertainty, missing checkpoint, narrative drift | `REQUIRE_HUMAN` |
| confidence < 0.42 | `FREEZE` |
| replay drift / chain verify fail / signature mismatch | `HARD_FREEZE` |

## Escalation ladder

```text
ALLOW -> ALLOW_WITH_CHECKPOINT -> SOFT_DENY -> REQUIRE_HUMAN -> FREEZE -> HARD_FREEZE
```

De-escalation MUST require fresh evidence (`verify` and `replay` coherent).

## Audit requirements per decision

| Decision | Required artifacts |
|----------|--------------------|
| ALLOW* | `MUTATION_POLICY_DECIDED`, post-validation event, sealed block |
| SOFT_DENY / REQUIRE_HUMAN | abort reason + checkpoint + sealed aborted block (when available) |
| FREEZE / HARD_FREEZE | explicit reason, no silent fallback, incident trail |

