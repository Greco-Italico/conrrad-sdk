# Causality Glossary v0

> **Institutional semantic kernel (handbook):** [`../../docs/handbook/GLOSSARY.md`](../../docs/handbook/GLOSSARY.md) — normative · prohibited · invalid equivalences across handbook/runtime/SDK.

| Term | Definition | Primary Evidence |
|------|------------|------------------|
| Habitat | Sovereign runtime boundary with identity, ledger, authority | `identity` API + habitat files |
| `habitat_id` | Stable operational identity of one habitat | `.conrrad_habitat.json` |
| `passport_id` | Habitat credential bound to identity lineage | `.conrrad_habitat.json` |
| Genesis | First sealed RuntimeBlock (`sequence=1`) | ledger head/tail |
| RuntimeBlock | Minimal signed causal truth unit | `.conrrad_runtime_blocks.jsonl` |
| Lineage | Append-only causal history across mutations/epochs | blocks + timeline |
| Lineage fork | Detectable divergence between causal sources | verify/replay mismatch |
| Silent lineage fork | Divergence without explanatory event | forensics finding |
| Replay | Deterministic reconstruction from blocks | replay API |
| Replay drift | Replayed state diverges from active/verified state | replay vs verify/head |
| Continuity confidence | Probability operational continuity is coherent | physiology fields |
| `stable_continuity` | High-confidence continuity state | block physiology |
| `unsafe_continuity` | Continuity cannot be safely trusted | block physiology + policy |
| Recovery posture | Operational restore safety posture (`stable/recovering/uncertain/unsafe`) | restore + physiology |
| Truthful restore | Restore constrained by evidence, not prose | restore payload audit |
| Epoch | Causal generation boundary (`continuity_epoch_id`) | propagation/blocks |
| Epoch mortality | Legitimate epoch death/rotation | epoch meta + events |
| Mutation authority | Server orchestrator as sole mutation gate | orchestrator API |
| `DEGRADED_BUT_HONEST` | System degraded but truth-preserving | chaos verdict + evidence |
| Integrity gap | Truth layer compromised; expansion blocked | verify/replay/conflict |

## Non-equivalences (critical)

- memory != truth
- cache != truth
- tool success != mutation truth
- executed != validated != sealed
- UI coherence != causal coherence

