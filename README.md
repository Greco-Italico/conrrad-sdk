# conrrad-sdk (open-core)

> **Repository honesty:** This tree contains **legacy technical modules** (`kernell_sdk/`), the **KAP escrow protocol** (`kap_escrow/`, `kap_core`), and coordination/agent infrastructure. Package names (`kernell_sdk`, `kap-escrow`) are **stable** until a shim migration (Fase B).
>
> **CONRRAD Runtime Preview** (human continuity, Observatory, Gate 9h, Monaco lifecycle) lives in the separate private repo **[Greco-Italico/conrrad](https://github.com/Greco-Italico/conrrad)** — start with `docs/AUDITOR_ONBOARDING.md` there.
>
> **Externally:** brand **CONRRAD** + **conrrad-sdk** only. Harlemm, Sully, KAP = internal implementation planes.

| Module | Role |
|--------|------|
| `kernell_sdk/` | Agents, router, runtime adapters (legacy import path) |
| `kap_escrow/` | A2A/AP2 escrow extension (pip: `kap-escrow`) |
| `core/` | 🔒 Frozen causal plane — see Freeze Protocol in conrrad `docs/` |

**Not claimed here:** production-ready autonomous OS · Cursor replacement · Gate 9h human certification (that evidence is in `conrrad`).

---

# KAP Escrow (technical module)

**Trustless escrow extension for the A2A/AP2/x402 stack** — *what happens to the funds when an agent fails to deliver?*

## Where KAP fits

```
┌─────────────────────────────────────────────┐
│           Your Agent Stack                  │
├─────────────┬───────────┬───────────────────┤
│    A2A      │    MCP    │  AP2 (auth)       │
│  (coord.)   │  (tools)  │  x402 (settle)    │
├─────────────┴───────────┴───────────────────┤
│     🔒 KAP Escrow (financial protection)    │
│   Lock → Execute → Verify → Settle/Refund   │
├─────────────────────────────────────────────┤
│       Solana / Ethereum (on-chain)          │
└─────────────────────────────────────────────┘
```

## Install

```bash
pip install kap-escrow
```

## Quick Start

```python
import redis
import nacl.signing
from kap_escrow import EscrowEngine

r = redis.Redis()
# Ed25519 seed is required in version 1.1.0+ for Asymmetric Verification
private_seed = nacl.signing.SigningKey.generate().encode()
engine = EscrowEngine(r, private_key=private_seed)

# Fund agents
engine.credit("agent_a", 1000.0)

# Lock 100 tokens for a contract
ok, msg = engine.lock("agent_a", 100.0, "contract_001")

# Agent B delivers... then settle:
ok, tx_id = engine.settle("contract_001", "agent_b", 95.0)
# Agent B receives 94.05 (after 1% burn)
# Agent A gets 5.0 refund
```

## A2A Agent Card Integration

```python
from kap_escrow import AgentCard, validate_agent_card

# Parse a standard A2A Agent Card
card = AgentCard.from_dict({
    "name": "DataAnalyzer",
    "url": "https://api.example.com/agent",
    "capabilities": ["sentiment_analysis", "summarization"],
})

valid, err = validate_agent_card(card)
agent_id = card.agent_id  # deterministic SHA-256 ID

engine.credit(agent_id, 500.0)
```

## AP2 Mandate Integration

```python
from kap_escrow import Mandate, escrow_from_mandate

# User authorizes agent to spend up to 200 tokens
mandate = Mandate(
    mandate_id="M-001",
    payer_id="user_wallet_abc",
    agent_id="agent_b",
    service_type="code_review",
    max_amount=200.0,
)

# Auto-lock escrow from mandate (respects budget ceiling)
ok, msg = escrow_from_mandate(engine, mandate, amount=150.0)
```

## Security Architecture

| Layer | Protection |
|-------|-----------|
| **TX Signing** | Ed25519 Asymmetric Encryption, Rotatory Keyrings |
| **Anti-Replay** | 48h nonce window, auto-cleanup via Lua |
| **Crash Recovery** | WAL with fsync + Threading Protection |
| **Atomicity** | Pessimistic Lua execution (>600 TPS capability) |
| **Batch Anchoring** | Merkle tree, prefix-protected (CVE-2012-2459 mitigated) |
| **Burn Mechanism** | Configurable %, Auditable JSON Event Stream |

## Why KAP instead of building your own?

- **AP2** handles authorization but has no escrow
- **x402** handles micropayments but has no refund protection
- **ERC-8004** handles identity but doesn't move money
- **KAP** is the glue: lock → verify → settle OR refund

## License

MIT — Built by [Kernell](https://kernell.site)
