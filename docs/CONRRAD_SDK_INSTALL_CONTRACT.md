# CONRRAD SDK Install Contract v0

| Campo | Valor |
|-------|--------|
| **Versión** | `SDK_INSTALL_CONTRACT_V0` |
| **Estado** | Contrato congelado — implementación incremental permitida |
| **Constitución** | [`CONRRAD_DOCTRINE_CONTRACT.md`](./CONRRAD_DOCTRINE_CONTRACT.md) |
| **Degradación** | [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md) |
| **Comando objetivo** | `./scripts/conrrad install` · futuro `npx conrrad install` |

`conrrad install` **no** instala librerías. Instala un **Habitat soberano**: entidad operacional con nacimiento verificable, identidad, ledger y autoridad constitucional.

---

## 0. Tesis

| Instalación tradicional | Instalación CONRRAD |
|-------------------------|---------------------|
| dependencias en `node_modules` | identidad + genesis + ledger |
| proyecto = carpeta | proyecto = habitat |
| git = herramienta externa | mutaciones = orchestrator SSOT |
| memoria = cache volátil | continuidad = evidencia persistente |

**Post-condición:** el directorio deja de ser "un repo" y pasa a ser **Habitat** con historia causal auditable.

---

## 1. Primitivo `conrrad install`

### Entrada

- workspace root vacío o existente (sin habitat previo, o con flag explícito de re-genesis prohibido en v0)
- permisos de escritura local
- Node runtime disponible

### Salida (obligatoria)

Tras install exitoso **deben existir**:

| Artefacto | Ruta | Propósito |
|-----------|------|-----------|
| Habitat meta | `.conrrad_habitat.json` | `habitat_id`, `passport_id`, schema |
| Signing key | `.conrrad_habitat.key` | HMAC seals (0600) |
| Genesis block | `.conrrad_runtime_blocks.jsonl` (seq 1) | nacimiento verificable |
| Head cache | `.conrrad_runtime_blocks.head.json` | head derivado |
| Evidence dir | `.conrrad_chaos_reports/` | reports · snapshots |
| Orchestrator | server routes live | `POST /api/mutations/orchestrate` |
| Authority lock | git POST → 403 | bypass imposible |

### Post-condición verificable

```bash
curl -s http://127.0.0.1:23817/api/runtime/blocks/identity   # habitat_id + passport_id
curl -s http://127.0.0.1:23817/api/runtime/blocks/verify      # ok: true
curl -s -X POST .../api/git/stage                               # 403 USE_MUTATION_ORCHESTRATOR
```

Install **falla** si cualquiera falla.

---

## 2. Ceremonia de génesis (orden fijo)

```text
1. HabitatIdentityV0     → habitat_id + passport_id + key
2. Genesis RuntimeBlock  → sequence 1, trigger genesis, confidence 1.0
3. Orchestrator mount    → mutation authority server-side
4. Physiology bootstrap  → confidence engine + epoch seed
5. Recovery bootstrap    → restore contract wired (ContinuityRestoreV1)
6. Evidence bootstrap    → snapshot/report dirs
7. Verify gate           → chain verify OK antes de "ready"
```

**Regla:** no existe habitat "ready" sin genesis verificable.

---

## 3. Árbol ontológico del habitat

Contrato de layout (paths lógicos; implementación puede consolidar):

```text
habitat/
├── passport/
│   ├── .conrrad_habitat.json      # identity meta
│   └── .conrrad_habitat.key       # signing key (0600)
├── genesis/
│   └── genesis.block              # = sequence 1 in ledger (logical view)
├── runtime/
│   ├── .conrrad_runtime_blocks.jsonl
│   └── .conrrad_runtime_blocks.head.json
├── lineage/
│   └── timeline + mission lineage stores
├── orchestrator/
│   └── server mutation authority (SSOT)
├── physiology/
│   └── confidence · epoch · pulse
├── recovery/
│   └── ContinuityRestoreV1 · resume engine
└── evidence/
    └── .conrrad_chaos_reports/
```

En v0 actual muchos paths viven en workspace root — el contrato define **semántica**, no refactor obligatorio inmediato.

---

## 4. Passport generation

### Contrato `HabitatIdentityV0`

```json
{
  "habitat_id": "hab_<fingerprint>",
  "passport_id": "psp_v0_<hash16>",
  "created_at": "<ISO8601>",
  "schema": "HabitatIdentityV0"
}
```

### Reglas

- `habitat_id` derivado de workspace fingerprint — estable por root path
- `passport_id` derivado criptográficamente del habitat — no reutilizable entre habitats
- clave 32 bytes aleatoria — nunca commitear
- rotación de clave requiere evento explícito (futuro P1) — no silent rotate

### Falsificación

| Check | Pass |
|-------|------|
| identity API == meta file | IDs coinciden |
| genesis firmado con misma key | verify OK |
| re-install sin flag | no sobrescribe identity silenciosamente |

---

## 5. Genesis block

### Contrato `RUNTIME_BLOCK_V1` sequence 1

Campos mínimos:

- `type`: `RUNTIME_BLOCK_V1`
- `sequence`: `1`
- `trigger`: `genesis`
- `prev_hash`: 64× `'0'`
- `habitat_id`, `passport_id`: de identity
- `physiology.continuity_confidence`: `1.0` (baseline epistemológico — no implica infalibilidad futura)
- `block_hash`, `signature`: HMAC habitat key

### Reglas

- genesis es **inmutable** — no edit in place
- re-genesis = **nuevo habitat** (nuevo lineage) — no "reset silencioso"
- verify debe pasar con código en disco **y** en RAM

---

## 6. Orchestrator initialization

Install debe garantizar:

1. **`POST /api/mutations/orchestrate`** — única vía de mutación constitucional
2. **`POST /api/git/*`** — bloqueado (`403 constitutional_mutation_required`)
3. **Chain verify gate** pre-mutation en server orchestrator
4. **RuntimeBlock seal** post-mutation exitosa

Debug bypass (`CONRRAD_ENFORCE_ORCHESTRATED_MUTATIONS=false`) — **solo dev local**, nunca production/P0.7.

---

## 7. Physiology & recovery bootstrap

Install wire mínimo:

| Módulo | Función |
|--------|---------|
| `continuityConfidenceEngine` | confidence fisiológica |
| `continuityEpoch` | epoch id + regression gate |
| `RecoveryRuntimeOrchestrator` | mutation lifecycle |
| `ContinuityRestoreV1` | restore truthful |
| snapshot loop (opcional) | evidencia longitudinal |

Install **no** promete confidence alta permanente — promete **medición honesta**.

---

## 8. Evidence bootstrap

Directorios mínimos:

- `.conrrad_chaos_reports/` — chaos + alpha artifacts
- physiology snapshots path configurable por sesión P0.7

Install puede registrar genesis evidence report:

```text
.conrrad_chaos_reports/genesis_<timestamp>/
  identity.json
  verify.json
  head.json
```

---

## 9. Idempotencia & prohibiciones

### Idempotente (safe)

- verificar identity existente y skip re-genesis
- verify chain si ya existe
- ensure dirs

### Prohibido en v0

- silent re-genesis sobre habitat con history
- install que sobrescribe `.conrrad_runtime_blocks.jsonl` sin evento
- install sin verify gate
- install que deja git POST abierto

### Re-genesis explícito (futuro P1)

Requiere:

- operador humano
- export forense previo
- evento `HABITAT_REGENESIS_REQUESTED`
- nuevo `habitat_id` — **no** continuidad con lineage anterior

---

## 10. Relación con degradación

Un habitat recién instalado está en:

- `confidence_state`: `stable_continuity` (baseline)
- `recovery_posture`: `stable`
- veredicto esperado post-chaos: `SURVIVED_ALL_ATTACKS` o `DEGRADED_BUT_HONEST`

**Epoch mortality** (restart) no invalida install — debe preservar genesis + chain verify.

Ver [`CONRRAD_DEGRADATION_SEMANTICS.md`](./CONRRAD_DEGRADATION_SEMANTICS.md) § epoch mortality.

---

## 11. SDK público vs install local

| Capa | Rol |
|------|-----|
| `conrrad-sdk` (PyPI/npm futuro) | distribución · APIs cliente |
| `conrrad install` | ceremonia de génesis on-host |
| Observatory | authority server-side |
| Dashboard | superficie humana — **no** SSOT |

El SDK consume el habitat; **no** redefine identidad ni autoridad.

---

## 12. Checklist de conformidad install

```text
[ ] .conrrad_habitat.json + .conrrad_habitat.key exist
[ ] genesis block sequence 1 verify OK
[ ] /api/runtime/blocks/identity responde
[ ] /api/runtime/blocks/verify ok: true
[ ] POST /api/git/stage → 403
[ ] POST /api/mutations/orchestrate acepta mutación válida
[ ] post-mutation RUNTIME_BLOCK_SEALED (seq ≥ 2 tras actividad)
[ ] ContinuityRestoreV1 wired (no mock restore)
[ ] evidence dirs creados
```

---

## 13. Violaciones del contrato

Install viola contrato si:

- produce habitat sin genesis verificable
- permite bypass git en modo "production"
- confla install con "npm install dependencies"
- reutiliza passport entre workspaces distintos
- oculta fallo verify en "ready" state

---

*Un SDK que no puede demostrar su propio nacimiento no puede pedir confianza operacional.*
