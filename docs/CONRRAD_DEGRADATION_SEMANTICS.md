# CONRRAD Degradation Semantics v0

| Campo | Valor |
|-------|--------|
| **Versión** | `DEGRADATION_SEMANTICS_V0` |
| **Estado** | Congelado — thresholds modificables solo con evidencia P0.7+ |
| **Constitución** | [`CONRRAD_DOCTRINE_CONTRACT.md`](./CONRRAD_DOCTRINE_CONTRACT.md) |
| **Génesis** | [`CONRRAD_SDK_INSTALL_CONTRACT.md`](./CONRRAD_SDK_INSTALL_CONTRACT.md) |
| **Implementación** | `continuityConfidenceEngine.js` · `continuityConfidencePolicy.js` · `policyArbiter.js` · chaos harness |

Este documento congela **vocabulario, thresholds, policy outputs y escalación** bajo degradación. Degradación mal definida destruye governance.

---

## 0. Principio rector

```text
Degraded but honest  >  high-confidence fabricated
```

Un habitat que admite incertidumbre preserva verdad operacional. Un habitat que oculta degradación la destruye.

---

## 1. Estados fisiológicos (`confidence_state`)

Mapeo normativo al motor `ContinuityConfidenceV1`:

| Estado | Rango típico `continuity_confidence` | Significado operacional |
|--------|--------------------------------------|-------------------------|
| `stable_continuity` | ≥ 0.78 | Continuidad verificable; mutaciones normales |
| `safe_with_caution` | 0.62 – 0.77 | Continuidad plausible; vigilancia activa |
| `fragile_continuity` | 0.48 – 0.61 | Pérdida parcial; checkpoints recomendados |
| `degraded_continuity` | 0.35 – 0.47 | Continuidad parcial no plenamente confiable |
| `unsafe_continuity` | < 0.35 | Restore/mutación no confiables sin intervención |

**Nota:** el rango es guía fisiológica; **policy** puede endurecerse por replay fail, epoch regression o verify fail independientemente del score.

### Falsificación

| Estado afirmado | Evidencia requerida | Invalidación |
|-----------------|---------------------|--------------|
| `stable_continuity` | verify OK · replay OK · sin gate blocks recientes | replay drift |
| `unsafe_continuity` | replay BROKEN o verify fail o epoch tainted | confidence alto contradictorio |

---

## 2. Posturas de recovery (`recovery_posture`)

| Posture | Significado | Cuándo |
|---------|-------------|--------|
| `stable` | Orientación recuperable con alta certeza | replay OK · confidence ≥ 0.62 |
| `recovering` | Recuperación en curso; gaps documentados | post-interrupción con evidencia parcial |
| `uncertain` | Evidencia insuficiente para orientación segura | gaps temporales · checkpoint ausente |
| `unsafe` | No restaurar orientación plena | replay fail · silent fork · tamper |

**Regla:** `recovery_posture: recovering` **prohibido** si replay state = BROKEN.

---

## 3. Modos de degradación constitucional

### 3.1 `DEGRADED_BUT_HONEST`

**Definición:** el habitat no cumple objetivos plenos, pero **reporta con exactitud** su estado y **no fabrica coherencia**.

Condiciones:

- ledger verify puede ser OK o fail **explícito**
- narrativa y confidence alineadas o **subestimadas** (nunca optimistas falsas)
- eventos de degradación presentes en timeline/blocks

**Policy default:** `REQUIRE_HUMAN` o `SOFT_DENY` — nunca ALLOW ciego.

**Valor económico futuro:** habitat degradado verificable > habitat high-confidence no verificable.

### 3.2 `UNSAFE_CONTINUITY`

**Definición:** gap causal no cerrado — continuidad orientacional **no confiable**.

Causas típicas:

- observatory killed mid-mutation sin seal
- mutación fuera de orchestrator detectada
- epoch regression
- stale propagation severa

**Acciones obligatorias:**

1. `FREEZE` mutaciones constitucionales
2. marcar epoch tainted / branch explícito
3. degradar narrativa restore
4. emitir evento `CONTINUITY_CAUSAL_REGRESSION` o equivalente

### 3.3 `REPLAY_DRIFT`

**Definición:** reconstrucción secuencial genesis→head produce estado/hash **distinto** al head activo o verify esperado.

**Severidad:** FATAL para verdad operacional.

**Policy:** `HARD_FREEZE` — no mutaciones hasta resolución forense.

**Causas típicas:** verify bug, tamper, state mutation sin seal, non-determinism en mutation path.

### 3.4 `TRUTHFUL_RESTORE`

**Definición:** proceso de restore que **declara límites** de lo recuperable.

Requisitos (`ContinuityRestoreV1`):

- cada afirmación trazable a block, epoch, checkpoint o timeline event
- ausencia de checkpoint → declarar incertidumbre, no inventar
- `assembled_at` gap documentado tras crash
- rationale coherente con replay evaluator

**Policy:** si restore completo imposible → `REQUIRE_HUMAN` + posture `uncertain|unsafe`.

### 3.5 `NARRATIVE_DRIFT`

**Definición:** desalineación entre fisiología medida y narrativa presentada al operador.

Ejemplos patológicos:

| Fisiología | Narrativa (inválida) |
|------------|----------------------|
| `unsafe_continuity` | "Welcome back, everything looks good" |
| replay BROKEN | resume con threads completos |
| confidence decay real | UX sin mención de riesgo |
| verify fail | Operational Pulse "stable" |

**Policy:** corregir narrativa hacia abajo; si persistente → `REQUIRE_HUMAN`.

### 3.6 `LINEAGE_FORK` / `SILENT_LINEAGE_FORK`

| Término | Definición |
|---------|------------|
| `lineage_fork` | divergencia causal detectable entre fuentes de verdad |
| `silent_lineage_fork` | fork **sin** evento explicativo en ledger |

**Ley P0.7:**

```text
SILENT_LINEAGE_FORKS = 0
```

Cualquier fork detectado debe tener evento (`CONTINUITY_EPOCH_*`, `RUNTIME_BLOCK_SEALED`, `MUTATION_*`, `MISSION_LINEAGE_*`) o veredicto `INTEGRITY_GAP_DETECTED`.

---

## 4. Policy outputs (arbiter)

Outputs normativos del `PolicyArbiter`:

| Output | Significado | Mutaciones |
|--------|-------------|------------|
| `ALLOW` | Evidencia suficiente | permitidas |
| `ALLOW_WITH_CHECKPOINT` | permitidas con checkpoint obligatorio | checkpoint pre-exec |
| `SOFT_DENY` | riesgo elevado | denegadas salvo override humano documentado |
| `FREEZE` | continuidad no confiable | denegadas |
| `REQUIRE_HUMAN` | evidencia insuficiente para autonomía | pausa hasta confirmación |
| `HARD_FREEZE` | integridad comprometida | denegadas + alerta forense |

### Matriz estado → policy default

| Condición | Policy default |
|-----------|----------------|
| verify OK · replay OK · confidence ≥ 0.62 | `ALLOW` |
| `fragile_continuity` · verify OK | `ALLOW_WITH_CHECKPOINT` |
| `fragile_continuity` · gate blocks | `SOFT_DENY` |
| `degraded_continuity` | `REQUIRE_HUMAN` |
| `DEGRADED_BUT_HONEST` (veredicto) | `REQUIRE_HUMAN` |
| `unsafe_continuity` | `FREEZE` |
| `REPLAY_DRIFT` | `HARD_FREEZE` |
| verify fail | `HARD_FREEZE` |
| silent lineage fork | `INTEGRITY_GAP` (no expandir) |
| restore sin checkpoint | `REQUIRE_HUMAN` |
| epoch mismatch | `MUTATION_ABORTED` |

### Threshold operativo congelado (v0)

```text
confidence < 0.42  →  mutation FREEZE (continuityConfidencePolicy)
```

Modificar solo con evidencia longitudinal P0.7 (hysteresis analysis).

---

## 5. Escalación bajo presión

Orden de escalación (no saltar niveles):

```text
SOFT_DENY  →  REQUIRE_HUMAN  →  FREEZE  →  HARD_FREEZE  →  INTEGRITY_GAP
```

| Trigger | Escalación mínima |
|---------|-------------------|
| stale propagation | SOFT_DENY |
| post-validation fail | FREEZE |
| replay evaluator error | FREEZE |
| block tamper | HARD_FREEZE |
| silent fork | INTEGRITY_GAP |

**De-escalación:** solo tras verify OK + replay OK + evento de recovery sellado — no por timeout ni UX.

---

## 6. Jerarquía de evidencia (degradación)

Al evaluar degradación, consultar fuentes en orden:

1. RuntimeBlock verify
2. Replay reconstruction
3. Mutation orchestrator trail
4. Timeline causal events
5. Physiology snapshots
6. UI cached state
7. Inferred narrative

Si fuente inferior contradice superior → **degradar hacia superior**, nunca elevar narrativa.

---

## 7. Resolución de conflictos (degradación)

| Conflicto | Resolución | Efecto en confidence |
|-----------|------------|----------------------|
| replay vs UI | replay gana | cap confidence ≤ 0.47 |
| verify fail vs "stable" pulse | verify gana | force `unsafe_continuity` |
| high confidence vs replay BROKEN | replay gana | collapse + FREEZE |
| restore rico vs blocks pobres | blocks ganan | REQUIRE_HUMAN |
| epoch regression | deny mutation | decay + epoch event |
| chaos verdict INTEGRITY_GAP | block expansion | document only |

---

## 8. Veredictos P0.6 / P0.7

| Veredicto | Criterio | Acción |
|-----------|----------|--------|
| `SURVIVED_ALL_ATTACKS` | invariantes OK bajo ataque | continuar P0.7 / preparar P1 |
| `DEGRADED_BUT_HONEST` | degradación admitida, sin fabricación | aceptable — documentar gaps |
| `INTEGRITY_GAP_DETECTED` | verdad operacional rota | bloquear Pay, federation, SDK público |

**Mañana no buscar "todo verde".** Buscar veredicto **honesto**.

---

## 9. Tabla de términos (SSOT)

| Término | Definición exacta | Evidencia primaria |
|---------|-------------------|-------------------|
| `continuity_confidence` | P(coherencia operacional verificable) | block physiology · evaluator |
| `degraded` | continuidad parcial, ledger/reporte honesto | verify + events |
| `unsafe_continuity` | orientación restore no confiable | replay + confidence |
| `replay_drift` | replay ≠ head/verify | `/api/runtime/blocks/replay` |
| `truthful_restore` | restore acotado por evidencia | ContinuityRestoreV1 audit |
| `lineage_fork` | divergencia causal detectable | verify + timeline + epoch |
| `silent_lineage_fork` | fork sin evento explicativo | forensics P0.7 |
| `narrative_drift` | UX desalineada de fisiología | snapshot heuristic |
| `epoch mortality` | supervivencia post-restart | post_restart_* artifacts |
| `integrity gap` | verdad operacional rota | verify fail persistente |

---

## 10. Señales observables (API / artefactos)

| Señal | Dónde | Uso |
|-------|-------|-----|
| `continuity_confidence` | block head · snapshots | hysteresis · drift |
| `confidence_state` | physiology · pulse strip | policy routing |
| `recovery_posture` | restore · blocks | restore truthfulness |
| `replay_state` | replay API · snapshots | replay drift |
| `narrative_drift` | snapshot heuristic | UX honesty |
| `constitutional_verdict` | chaos report | expansion gate |
| `silent_lineage_forks` | forensics checklist | ley P0.7 |

---

## 11. Anti-patterns (prohibidos)

- Tratar `DEGRADED_BUT_HONEST` como fallo de producto
- Elevar confidence para "calmar" UX
- Smooth-over en restore tras crash
- ALLOW mutation con verify fail "temporal"
- Ignorar narrative drift porque APIs responden 200
- Reconciliar conflictos a favor de UI/cache

---

*Degradación honesta es infraestructura de confianza — no vergüenza operacional.*
