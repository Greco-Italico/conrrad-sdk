# Integrity Gap Playbook v0

## Objective

Provide a deterministic response when operational truth is suspected compromised.

`INTEGRITY_GAP_DETECTED` means:
- do not expand scope,
- do not normalize failure,
- do not restore confidence by narrative.

## Triggers

- `verify.ok == false` with integrity or chain issues
- replay drift (`replay` inconsistent with head/verify)
- detected or suspected silent lineage fork
- restore contradiction (claims not supported by blocks/timeline)
- mutation accepted while authority gate should block

## Immediate response (first 5 minutes)

1. Freeze constitutional mutations (`FREEZE`/`HARD_FREEZE` path).
2. Capture evidence bundle:
   - identity
   - verify
   - head
   - replay
   - latest timeline slice
3. Probe bypass gate (`POST /api/git/stage`) and record status.
4. Record current verdict as provisional `INTEGRITY_GAP_DETECTED`.

## Evidence bundle template

```text
<out>/
  identity.json
  verify.json
  head.json
  replay.json
  timeline.json
  git_bypass_probe.txt
  notes.md
```

## Classification rules

| Case | Verdict |
|------|---------|
| Degradation admitted, evidence coherent | `DEGRADED_BUT_HONEST` |
| Integrity conflict unresolved or silent fork | `INTEGRITY_GAP_DETECTED` |
| No contradiction after verification | `SURVIVED_ALL_ATTACKS` |

## Recovery path

1. Identify root contradiction source (hash/signature/link/replay/event order).
2. Fix code/config cause (never rewrite old truth to hide issue).
3. Restart runtime if RAM/disk semantic mismatch suspected.
4. Re-run verify + replay + authority probe.
5. Downgrade verdict only with evidence, not elapsed time.

## Prohibitions

- No in-place ledger rewriting to force green state.
- No mutation execution while unresolved integrity gap persists.
- No optimistic restore copy that exceeds replay certainty.
- No feature work on top of unresolved truth conflicts.

