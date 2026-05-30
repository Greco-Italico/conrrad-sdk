# CONRRAD SDK (public surface)

**Sovereign operational infrastructure for verifiable autonomous systems.**

| Resource | Link |
|----------|------|
| **Developer documentation (SSOT)** | [`docs/README.md`](docs/README.md) |
| Doctrine Contract | [`docs/CONRRAD_DOCTRINE_CONTRACT.md`](docs/CONRRAD_DOCTRINE_CONTRACT.md) |
| Degradation Semantics | [`docs/CONRRAD_DEGRADATION_SEMANTICS.md`](docs/CONRRAD_DEGRADATION_SEMANTICS.md) |
| This repo | [GitHub](https://github.com/Greco-Italico/conrrad-sdk) |

> **Documentation is operational infrastructure.** SDK changes without doc updates are invalid — see [`docs/DOCS_GOVERNANCE.md`](docs/DOCS_GOVERNANCE.md).

> **Open core:** this repository is the **public SDK surface** for CONRRAD (`conrrad-sdk` on PyPI — migrating from legacy `kap-escrow`). Full runtime, orchestrator, and HARLEMM live in the private `conrrad` implementation. Do not use “KAP” or “Kernell” in new public copy.

---

## Operational inference gate (architectural center)

CONRRAD does **not** treat the LLM as the system. The LLM is a scarce probabilistic coprocessor inside a governed causal runtime.

```text
user → CONRRAD runtime → (replay? primitive? skill? certainty? authority? continuity? drift?)
  → ONLY IF NEEDED → LLM → runtime verifies · captures · operationalizes
```

**Law:** *Inference should be exceptional.*  
Read: [`docs/CONRRAD_DEGRADATION_SEMANTICS.md`](docs/CONRRAD_DEGRADATION_SEMANTICS.md)

---

## What is CONRRAD?

CONRRAD is a **sovereign operational ecosystem** for autonomous systems.

It combines:

- constitutional runtime governance
- replayable operational truth
- economically-aware AI execution
- local-first survivability
- autonomous work execution
- verifiable settlement
- federation-ready habitats

into a single operational environment.

Most AI systems optimize for:

- capability
- demos
- orchestration
- interface design

CONRRAD optimizes for:

- operational truthfulness
- replayability
- economic survivability
- autonomous governance
- honest degradation under real-world conditions

## The Core Thesis

Modern AI systems suffer from structural problems:

- agents fabricate continuity
- operational history is mutable or opaque
- memory is treated as truth
- tool success is assumed to mean correctness
- AI costs scale uncontrollably
- autonomous execution lacks accountability
- marketplaces cannot verify operational integrity
- systems collapse silently under degradation

CONRRAD exists to solve these problems.

The system may degrade.

The objective is to make degradation:

- observable
- replayable
- governable
- economically measurable
- operationally honest

> Important: CONRRAD does **not** claim perfection or impossibility of deception.  
> It is designed to make deception/drift **detectable, governable, and reconstructible** through evidence.

---

## The CONRRAD Ecosystem

CONRRAD is not a single product.

It is a sovereign operational stack.

### 1) CONRRAD Runtime — Constitutional operational runtime

The Runtime is the causal core of the ecosystem.

It governs:

- RuntimeBlocks
- replay
- verify
- mutation authority
- epoch semantics
- lineage
- continuity confidence
- degradation semantics

The Runtime establishes:

- replayable operational truth
- append-only lineage
- mutation governance
- restart-aware continuity
- evidence-bounded recovery

Core principles:

- replay supremacy
- truthful degradation
- constitutional mutation authority
- operational evidence over narrative

### 2) CONRRAD IDE — AI-native sovereign development environment

CONRRAD IDE is the inhabitable surface of the ecosystem.

It is designed to provide:

- modern AI-native development workflows
- agentic coding assistance
- terminal orchestration
- workspace awareness
- multi-model execution
- local model integration
- operational replay awareness
- cost-conscious routing
- constitutional execution governance

The goal is not to remove developer control.

The goal is to create environments where:

- operations are observable
- mutations are governed
- costs are measurable
- autonomous execution becomes accountable

### 3) HARLEMM — Hyper-efficient autonomous runtime layer

HARLEMM exists to solve one of the largest structural problems in AI:

**uncontrolled operational cost.**

Most AI tooling continuously escalates inference usage by routing nearly every operation through premium models.

HARLEMM is designed around a different philosophy:

**the environment should understand as much as possible before escalating to expensive inference.**

HARLEMM focuses on:

- local-first survivability
- operational awareness
- intelligent routing
- inference minimization
- efficiency-aware orchestration
- constrained hardware execution
- autonomous operational optimization

The objective is not merely “cheaper AI”.

The objective is sustainable autonomous operation.

Longitudinal efficiency benchmarking is currently underway.

### 4) CONRRAD Marketplace — Autonomous operational work exchange

The Marketplace is an emerging layer where:

- humans
- agents
- habitats
- enterprises

can exchange operational work under verifiable conditions.

Future marketplace operations are designed to support:

- replay-backed execution
- mutation traceability
- operational scoring
- continuity confidence metrics
- autonomous task execution
- verifiable settlement
- evidence-linked reputation

The goal is not gig work with AI wrappers.

The goal is measurable operational trust between autonomous entities.

### 5) CONRRAD PAY — Verification-aware operational settlement

CONRRAD PAY is the operational settlement layer of the ecosystem.

It is designed for:

- execution-linked settlement
- replay-aware accountability
- escrow primitives
- verification receipts
- mutation-bound operational payment
- autonomous coordination

The ecosystem token is not designed as a speculative asset.

Its role is functional:

- operational coordination
- verification metering
- settlement utility
- governance signaling
- ecosystem routing

### 6) Federation Layer — Cross-habitat operational trust

CONRRAD habitats are designed to become sovereign operational entities capable of:

- exchanging proofs
- negotiating trust
- validating replayability
- preserving lineage
- coordinating under constitutional constraints

Federation primitives are currently in architectural development.

---

## Evidence, Not Demos

Most AI systems present:

- demos
- benchmark screenshots
- curated workflows
- isolated examples

CONRRAD is designed around **longitudinal operational evidence**.

Evidence is expected to be reconstructible from:

- RuntimeBlock verification
- deterministic replay
- mutation lifecycle reconstruction
- epoch continuity analysis
- authority enforcement
- degradation semantics
- operational telemetry

Core evidence primitives:

- verify
- replay
- mutation lifecycle
- continuity confidence
- replay drift detection
- authority integrity
- degraded-but-honest behavior

If a claim cannot survive verify + replay, CONRRAD does not treat it as operational truth.

---

## Operational Philosophy

| Traditional AI Systems | CONRRAD |
|---|---|
| memory-centric | replay-centric |
| narrative continuity | causal continuity |
| orchestration-first | constitutional-first |
| hidden degradation | degraded-but-honest |
| mutable operational history | append-only lineage |
| inference-first | operational-awareness-first |
| opaque autonomy | measurable autonomy |

---

## The CONRRAD Flywheel

More developers using the IDE  
↓  
More operational telemetry  
↓  
Better HARLEMM optimization  
↓  
Lower inference cost  
↓  
Higher survivability  
↓  
More autonomous execution  
↓  
More operational evidence  
↓  
More replay-backed trust  
↓  
More habitats federating  
↓  
More verifiable economic activity  
↓  
More developers entering the ecosystem

---

## Ecosystem Maturity

| Layer | Status |
|------|--------|
| Runtime primitives | Active |
| Verify + replay | Active |
| Mutation governance | Active |
| Epoch semantics | Active |
| Longitudinal evidence harness | Active |
| IDE integration | Experimental |
| HARLEMM optimization layer | Experimental |
| Marketplace architecture | Early architecture |
| Federation primitives | Research / architecture |
| Verification economics | Research |
| Autonomous settlement | Experimental |

---

## Open core (this repo)

This public repository is a **limited SDK surface** — not the constitutional runtime.

| In this repo (public) | In private core (not here) |
|------------------------|----------------------------|
| Adapter examples, KAP escrow spec | Full verify/replay engine |
| Habitat bootstrap docs | HARLEMM heuristics & routing |
| Protocol sketches | Telemetry corpora, settlement engine |

Policy: Refer to Institutional Access documentation provided to your organization.

---

## What CONRRAD Is Not

CONRRAD is not:

- another AI IDE
- another memory framework
- “vector database = continuity”
- another orchestration wrapper
- speculative AI token infrastructure
- “fully open source” constitutional runtime
- AGI marketing

---

## Current Direction

Current focus areas:

- longitudinal survivability testing
- replay integrity
- mutation governance hardening
- operational evidence generation
- HARLEMM efficiency benchmarking
- constitutional runtime stabilization
- autonomous operational accountability

---

## Next Steps

Explore the operational semantics and installation contract:

- [Installation Contract](docs/CONRRAD_SDK_INSTALL_CONTRACT.md)
- [Causality Glossary](docs/CAUSALITY_GLOSSARY.md)

Run the Playground locally to inspect the SDK:

- [Playground Quickstart](docs/playground/README.md)
- verify events
- replay causal history
- mutation authority
- degradation behavior

Evaluate operational truth under real conditions.

---

## Final Principle

CONRRAD does not assume autonomous systems are infallible.

It assumes they must become:

- replayable
- governable
- economically survivable
- operationally honest

