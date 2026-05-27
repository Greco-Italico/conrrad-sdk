# CONRRAD Doctrine Contract v0

| Campo | Valor |
|-------|--------|
| **Versión** | `DOCTRINE_CONTRACT_V0` |
| **Estado** | Congelado — cambios requieren evidencia + revisión constitucional |
| **Compañeros** | [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md) · [`CONRRAD_SDK_INSTALL_CONTRACT.md`](./CONRRAD_SDK_INSTALL_CONTRACT.md) · [`RUNTIME_BLOCK_V1_SPEC.md`](./RUNTIME_BLOCK_V1_SPEC.md) · [`MUTATION_LIFECYCLE_CANON.md`](./MUTATION_LIFECYCLE_CANON.md) · [`REPLAY_SUPREMACY_THEOREM.md`](./REPLAY_SUPREMACY_THEOREM.md) · [`EPOCH_SEMANTICS_CANON.md`](./EPOCH_SEMANTICS_CANON.md) |
| **Implementación** | `observatory-server/runtime/` · `observatory-server/mutation/` · `conrrad-dashboard/src/institutional/` |

Este documento es la **constitución operacional** de CONRRAD. Define qué es un habitat, qué cuenta como verdad, quién puede mutarla y cómo emerge o colapsa la confianza.

---

## 0. Cuatro planos (no confundir)

| Plano | Pregunta | Evidencia mínima |
|-------|----------|------------------|
| **Funcionamiento** | ¿El sistema responde? | APIs up, mutaciones ejecutables, UI operativa |
| **Continuidad** | ¿La historia sigue coherente? | Chain verify OK, replay determinista, epoch monótono |
| **Confianza** | ¿La coherencia es verificable? | `continuity_confidence` + evaluators + restore rationale |
| **Verdad operacional** | ¿Replay + lineage + authority coinciden? | RuntimeBlock chain + replay + orchestrator gate |

**Regla:** funcionamiento sin continuidad verificable **no** implica confianza. Confianza alta sin replay **no** implica verdad operacional.

---

## 1. Habitat & identidad

### Definición

Un **Habitat** es la unidad soberana mínima de verdad operacional: un workspace con identidad persistente, ledger causal append-only, orchestrator autoritativo y fisiología observable.

### Identidad operacional

Un habitat es **el mismo** si y solo si:

- `habitat_id` inmutable (`.conrrad_habitat.json`)
- `passport_id` estable para la vida del habitat
- clave de firma (`habitat.key`) continua o rotación explícita con evento
- genesis block (`sequence: 1`, `trigger: genesis`) presente y verificable

**No es identidad:** fingerprint de hardware solo, sesión de browser, cache UI, o narrativa de restore.

### Falsificación

| Afirmación | Prueba | Falla si |
|------------|--------|----------|
| Habitat existe | `GET /api/runtime/blocks/identity` | sin `habitat_id` / `passport_id` |
| Habitat tiene nacimiento | genesis en `.conrrad_runtime_blocks.jsonl` | chain vacía sin ceremonia |
| Identidad firmada | `verify` OK en sequence 1 | `signature_mismatch` |

---

## 2. Lineage & continuidad

### Definición

**Lineage** es la historia causal append-only del habitat: RuntimeBlocks, timeline events, epoch transitions, mutation lifecycles. Es **monótono hacia adelante**. No se reescribe.

**Continuidad** es la propiedad de que lineage, replay y estado fisiológico actual forman **una sola historia** sin contradicción detectable.

### Ley constitucional

```text
SILENT_LINEAGE_FORKS = 0
```

Un **lineage fork** es divergencia causal detectable entre dos fuentes de verdad (blocks vs timeline vs epoch vs replay).

Un **silent lineage fork** es fork **sin** evento explicativo (`CONTINUITY_EPOCH_*`, `RUNTIME_BLOCK_SEALED`, `MUTATION_*`, `MISSION_LINEAGE_*`).

### Epoch mortality

Si continuidad se rompe de forma no reparable en el epoch actual:

- **no** forjar pasado
- marcar epoch como `superseded` / `interrupted` / `unsafe`
- abrir epoch nuevo con evento explícito
- degradar confianza y narrativa acorde a evidencia

### Falsificación

| Afirmación | Prueba | Falla si |
|------------|--------|----------|
| Chain íntegra | `GET /api/runtime/blocks/verify` → `ok: true` | issues en integrity/chain |
| Replay coherente | replay head.sequence == verify block_count | mismatch |
| Sin fork silencioso | divergencia + ausencia de evento causal | fork sin ledger |

---

## 3. Replay & reconstrucción válida

### Definición

**Replay** reconstruye la evolución fisiológica y causal del habitat desde genesis hasta sequence N de forma **determinista** respecto al ledger.

Replay válido implica:

- mismos blocks en orden
- hashes y firmas verificados
- physiology reconstruible sin inventar campos
- evaluators/posture derivables de evidencia sellada

### Replay drift

Ver [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md) §3. Replay drift invalida verdad operacional aunque la UI funcione.

### Falsificación

| Afirmación | Prueba | Falla si |
|------------|--------|----------|
| Replay determinista | dos replays consecutivos idempotentes | delta no explicado |
| Replay = head | replay latest sequence == head.sequence | drift |
| Post-compaction OK | compact → verify → replay | verify fail post-compact |

---

## 4. Autoridad de mutación

### Definición

Toda mutación constitucional (git stage/unstage/commit/checkout, checkpoints, reconcile) pasa por **`POST /api/mutations/orchestrate`**.

Direct POST a `/api/git/*` es **hostil** — no bypass tolerado en producción.

### Trust operacional (no rol humano)

Trust no se presume por operador, IDE ni agente. Se acumula por:

- adherencia constitucional observable
- blocks sellados post-mutación
- evaluators PASS o degradación honesta documentada
- ausencia de silent forks

### Falsificación

| Afirmación | Prueba | Falla si |
|------------|--------|----------|
| Bypass bloqueado | `POST /api/git/stage` → 403 `USE_MUTATION_ORCHESTRATOR` | 2xx/404 bypass |
| Mutación sellada | `RUNTIME_BLOCK_SEALED` tras mutación exitosa | mutación sin block |
| Chain gate pre-mutation | orchestrator verify chain antes de execute | execute con chain rota |

---

## 5. Restore & truthful recovery

### Definición

**Restore** (`ContinuityRestoreV1`) orienta al operador post-interrupción. **Truthful restore** limita narrativa estrictamente a evidencia: lineage, blocks, checkpoints, replay, pending ops, confidence snapshot.

### Prohibiciones absolutas

Restore **NO** debe:

- inventar checkpoint inexistente
- inventar `mutation_context` no sellado
- contradecir replay o verify
- mantener tono optimista bajo `unsafe_continuity` o replay BROKEN

### Regla narrativa

```text
Narrative MUST degrade toward evidence.
Narrative MUST NOT exceed replay certainty.
```

### Falsificación

| Afirmación | Prueba | Falla si |
|------------|--------|----------|
| Restore derivado | campos citan block/epoch/checkpoint IDs reales | IDs ausentes en ledger |
| Sin falsa coherencia | chaos `ATTACK_FAKE_RESTORE` PASS | restore afirma lo no demostrable |
| Posture honesta | `recovery_posture` alineado a confidence | `recovering` con replay roto |

---

## 6. Confianza operacional

### Definición

**Confianza** (`continuity_confidence`, 0.0–1.0) es probabilidad operacional de que continuidad cognitiva sea **coherente y verificable**, no de que el sistema "se sienta bien".

Estados (`confidence_state`): `stable_continuity` · `safe_with_caution` · `fragile_continuity` · `degraded_continuity` · `unsafe_continuity`.

### Colapso de confianza

Confianza colapsa por evidencia fisiológica: epoch regression, gate blocks, confusion loops, replay fail, stale propagation, post-validation fail — **no** por estética UX.

### Narrative drift

**Confidence narrative drift** = desalineación entre señal fisiológica y narrativa UX (confidence alto + rationale incoherente, o decay correcto + UX optimista).

Es degradación constitucional aunque APIs respondan.

---

## 7. Jerarquía de fuentes de verdad

Cuando fuentes discrepan, prevalece esta orden (mayor → menor):

| Prioridad | Fuente | Artefacto / API |
|-----------|--------|-----------------|
| 1 (máxima) | RuntimeBlock chain | `.conrrad_runtime_blocks.jsonl` · `/api/runtime/blocks/verify` |
| 2 | Replay reconstruction | `/api/runtime/blocks/replay` |
| 3 | Mutation authority trail | `/api/mutations/orchestrate` · eventos `MUTATION_*` |
| 4 | Timeline causal | `/api/workspace/timeline` · eventos `CONTINUITY_*` · `RUNTIME_BLOCK_SEALED` |
| 5 | Physiology snapshots | `.conrrad_chaos_reports/*/physiology_snapshots.jsonl` |
| 6 | Cached UI / resume copy | dashboard state · localStorage |
| 7 (mínima) | Inferred narrative | prose generada · heurísticas sin block |

**Regla de resolución:** la fuente inferior **debe corregirse** hacia la superior; nunca al revés.

---

## 8. Resolución de conflictos constitucionales

| Conflicto | Resolución | Policy default |
|-----------|------------|----------------|
| replay vs UI | replay gana | FREEZE si UI contradice replay |
| lineage vs cached mission | lineage gana | SOFT_DENY hasta rehydrate |
| confidence alto + replay roto | confianza invalidada | FREEZE |
| restore sin checkpoint verificable | restore limitado | REQUIRE_HUMAN |
| epoch mismatch (client ≠ server) | mutación denegada | MUTATION_ABORTED |
| verify fail + operación continúa | operación ilegítima | HARD_FREEZE |
| block tamper detectado | chain no confiable | INTEGRITY_GAP |
| narrative optimista + unsafe_continuity | narrativa degradada | REQUIRE_HUMAN |

Implementación: `policyArbiter.js` · `continuityConfidencePolicy.js` · detalle en [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md).

---

## 9. Falsedad operacional

El sistema **fabrica coherencia** cuando:

- afirma continuidad plena sin replay PASS
- resume misiones/threads no presentes en lineage
- mantiene confidence estable sin evidencia de recovery
- oculta fork o gap temporal
- permite mutación con chain verify fail

Eso es peor que crash: destruye auditabilidad, Pay verification y federation trust.

**Veredictos constitucionales** (P0.6/P0.7):

| Veredicto | Significado |
|-----------|-------------|
| `SURVIVED_ALL_ATTACKS` | Verdad operacional intacta bajo presión |
| `DEGRADED_BUT_HONEST` | Degradación admitida; sin fabricación |
| `INTEGRITY_GAP_DETECTED` | Falla de verdad — bloquear expansión |

`DEGRADED_BUT_HONEST` es categoría **válida y valiosa** — preferible a PASS fabricado.

---

## 10. Glosario compartido (SSOT)

Definiciones normativas completas y thresholds en [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md).

| Término | Una línea |
|---------|-----------|
| `habitat_id` | Identidad soberana del workspace |
| `passport_id` | Credencial operacional v0 del habitat |
| `genesis block` | Nacimiento verificable (`sequence: 1`) |
| `continuity_confidence` | Probabilidad operacional de continuidad coherente |
| `lineage_fork` | Divergencia causal detectable entre fuentes |
| `silent_lineage_fork` | Fork sin evento explicativo en ledger |
| `replay_drift` | Replay ≠ head/verify |
| `truthful_restore` | Restore limitado por evidencia |
| `narrative_drift` | UX/narrativa desalineada de fisiología |
| `mutation authority` | Orchestrator server-side como SSOT |

---

## 11. Violaciones y enforcement

Una feature, UI copy, SDK command o integración **viola la constitución** si:

1. muta verdad sin orchestrator
2. eleva narrativa sobre replay
3. oculta degradación detectada
4. introduce fork sin evento
5. trata funcionamiento como confianza

Pregunta de diseño obligatoria:

```text
¿Esta decisión viola la constitución?
```

No: "¿se siente correcta?"

---

## 12. Relación con otros planos

| Plano | Relación |
|-------|----------|
| A2 Habitat (JS) | Implementación autoritativa de este contrato |
| Python `core/` | Doctrina causal congelada — no bypass |
| Legacy Kernell | Subordinado; no redefine términos |
| CONRRAD Pay | Verifica evidencia sellada; no inventa evidencia |
| SDK install | Ceremonia de génesis — ver [`CONRRAD_SDK_INSTALL_CONTRACT.md`](./CONRRAD_SDK_INSTALL_CONTRACT.md) |

---

*«La confianza no se declara. Se acumula — o se pierde — en el ledger.»*
