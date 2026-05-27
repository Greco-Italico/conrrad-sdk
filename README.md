# CONRRAD SDK

**Website:** `https://conrrad.online`  
**Core runtime + evidence repo:** `https://github.com/Greco-Italico/conrrad`  

CONRRAD is a **sovereign operational runtime** for **verifiable AI continuity**.

Most AI systems optimize for **capability** (demos, prompts, tools).  
CONRRAD optimizes for **operational truth**: replayable causality, mutation governance, epoch mortality, and honest degradation.

> The system may degrade. The goal is to make silent fabrication **detectable, governable, and replayable** — not to promise perfection.

---

## What makes CONRRAD different

| Typical AI runtime | CONRRAD |
|---|---|
| memory-centric | **replay-centric** |
| narrative continuity | **causal continuity** |
| tool success = assumed truth | **verify + replay required** |
| mutable / hidden history | **append-only lineage** |
| hidden degradation | **degraded-but-honest** |
| orchestration-first | **constitutional-first** |

---

## Evidence (not claims)

Most projects show curated demos. CONRRAD is built around **longitudinal operational evidence**.

Evidence is expected to be **reconstructible** from:

- **RuntimeBlock verification** (integrity + chain links)
- **Deterministic replay** (reconstruct what happened)
- **Mutation lifecycle** (requested → evaluated → policy → executed → post-validated → sealed)
- **Epoch semantics** (restart mortality; no silent continuity)
- **Honest degradation** (confidence + narrative collapse toward evidence)

CONRRAD does not assume operational truth from narrative, memory, or tool success.
It attempts to **bound continuity claims** to replayable and verifiable evidence.

If it cannot be replayed, it cannot be operationally trusted.  
If it cannot be verified, it cannot govern continuity.

### What you can verify today (from the runtime repo)

From `Greco-Italico/conrrad` (Observatory running):

- `GET /api/runtime/blocks/verify` — chain integrity
- `GET /api/runtime/blocks/replay` — reconstruction trail
- `POST /api/git/*` is expected to be blocked (authority enforcement)
- `POST /api/mutations/orchestrate` is the mutation SSOT

Key specs:

- RuntimeBlock spec: `docs/RUNTIME_BLOCK_V1_SPEC.md`
- Replay theorem: `docs/REPLAY_SUPREMACY_THEOREM.md`
- Mutation canon: `docs/MUTATION_LIFECYCLE_CANON.md`
- Epoch canon: `docs/EPOCH_SEMANTICS_CANON.md`
- Truth hierarchy: `docs/TRUTH_HIERARCHY.md`
- Integrity gaps playbook: `docs/INTEGRITY_GAP_PLAYBOOK.md`

---

## What “install” means

This SDK is not “a library wrapper”.

`conrrad install` is a **sovereign habitat genesis ceremony**:

- creates habitat identity (`habitat_id`, `passport_id`)
- seeds a genesis RuntimeBlock
- establishes signing authority for sealing
- mounts constitutional mutation authority gates
- ensures verify + replay readiness

Contract: `docs/CONRRAD_SDK_INSTALL_CONTRACT.md` (in the runtime repo).

**Important:** this does not eliminate deception or drift. It makes them **observable** and gives the system a place to record and govern them.

---

## Who this is for

CONRRAD is for teams building long-running autonomous systems that must be:

- auditable
- replayable
- governable under failure
- safe under restarts and drift

This includes enterprise automation, financial execution systems, regulated workflows, and safety-critical agentic infrastructure.

---

## Roadmap (evidence-first)

- **P0 / P0.7:** constitutional primitives + longitudinal integrity evidence  
- **P1:** federation + cross-habitat trust negotiation  
- **P2:** verification economics (proof-of-integrity receipts, metered verification)  

---

## Non-goals (what CONRRAD is not)

- not “another AI IDE”
- not “vector memory = truth”
- not “tool success = integrity”
- not “AGI platform” marketing

---

## Next steps

1. Read the doctrine/specs in the runtime repo (`Greco-Italico/conrrad` → `docs/CONSTITUTION_INDEX.md`).  
2. Run Observatory locally and inspect `verify` + `replay`.  
3. Evaluate degraded-but-honest behavior under restart + mutation pressure.

