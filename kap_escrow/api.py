import re
import secrets
import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, field_validator
from decimal import Decimal
import redis

from kap_escrow.taint import TaintLedger, TaintedAsset
from kap_escrow.escrow_v2 import FinCenEscrow, ExecutionContext

# ── Auth Layer (KOS-019) ──────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-KAP-API-Key")
_VALID_API_KEYS: set[str] = set()

def _load_api_keys():
    """Load API keys from environment. Multiple keys separated by commas."""
    raw = os.getenv("KAP_API_KEYS", "")
    if raw:
        for k in raw.split(","):
            k = k.strip()
            if k:
                _VALID_API_KEYS.add(k)

_load_api_keys()

async def require_api_key(key: str = Depends(API_KEY_HEADER)):
    """Validates API key for all financial endpoints."""
    if not _VALID_API_KEYS or not any(secrets.compare_digest(key, k) for k in _VALID_API_KEYS):
        raise HTTPException(status_code=401, detail="API key inválida o ausente")

# ── Agent ID validation (KOS-023) ─────────────────────────────────────
_AGENT_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{3,64}$')

def _validate_agent_id(agent_id: str) -> str:
    if not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=422, detail=f"agent_id inválido: {agent_id[:30]!r}")
    return agent_id

# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CONRRAD Payments API",
    description="The financial infrastructure for autonomous agents. FinCEN-grade compliant escrows.",
    version="1.0.0"
)

# Dependency to get Redis client
def get_redis():
    r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)
    yield r
    r.close()

# Dependency to get TaintLedger
def get_ledger(r: redis.Redis = Depends(get_redis)):
    return TaintLedger(r)

# ── Models with validation ────────────────────────────────────────────

class EscrowCreate(BaseModel):
    sender: str
    receiver: str
    amount: str
    contract_id: str

    @field_validator("sender", "receiver")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not _AGENT_ID_RE.match(v):
            raise ValueError(f"ID de agente inválido: {v[:30]!r}")
        return v

class EscrowSettle(BaseModel):
    contract_id: str
    payouts: dict[str, str]  # Example: {"agent_A": "50.0", "agent_B": "50.0"}

class MintRequest(BaseModel):
    agent_id: str
    amount: str
    is_tainted: bool = False

    @field_validator("agent_id")
    @classmethod
    def validate_agent(cls, v: str) -> str:
        if not _AGENT_ID_RE.match(v):
            raise ValueError(f"ID de agente inválido: {v[:30]!r}")
        return v

# ── /dev/mint — BLOCKED in production (KOS-019) ──────────────────────

if os.getenv("KERNELL_ENV") == "production":
    @app.post("/dev/mint")
    def dev_mint_blocked():
        raise HTTPException(status_code=403, detail="/dev/mint está deshabilitado en producción")
else:
    @app.post("/dev/mint", dependencies=[Depends(require_api_key)])
    def dev_mint(req: MintRequest, ledger: TaintLedger = Depends(get_ledger)):
        """DEV ONLY: Mints local KERN for an agent."""
        asset = ledger.get_taint(req.agent_id)
        mint_amount = Decimal(req.amount)
        
        clean = asset.clean_amount + (mint_amount if not req.is_tainted else Decimal("0"))
        tainted = asset.tainted_amount + (mint_amount if req.is_tainted else Decimal("0"))
        
        new_asset = TaintedAsset(clean, tainted, "mint_tx")
        ledger.set_taint(req.agent_id, new_asset)
        return {"status": "ok", "agent": req.agent_id, "new_total": str(new_asset.total_amount)}


# ── Escrow endpoints — auth required (KOS-019, KOS-020) ──────────────

@app.post("/escrow/create", dependencies=[Depends(require_api_key)])
def create_escrow(req: EscrowCreate, ledger: TaintLedger = Depends(get_ledger), r: redis.Redis = Depends(get_redis)):
    """Creates a Taint-Aware UTXO Escrow and locks funds — persisted in Redis."""
    escrow_key = f"kap:escrow:{req.contract_id}"
    
    # KOS-020: Check Redis for existing escrow instead of in-memory dict
    if r.exists(escrow_key):
        raise HTTPException(status_code=400, detail="Escrow already exists")

    amount = Decimal(req.amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    sender_asset = ledger.get_taint(req.sender)
    if sender_asset.total_amount < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # Escrow Setup
    ctx = ExecutionContext(ledger)
    
    # Simulate execution context locking funds
    ctx.load_participant(req.sender)
    from kap_escrow.taint import split_asset
    
    # Remove funds from sender's wallet to lock in escrow
    transferred = split_asset(ctx.initial_state[req.sender], amount)
    new_sender = TaintedAsset(
        ctx.initial_state[req.sender].clean_amount - transferred.clean_amount,
        ctx.initial_state[req.sender].tainted_amount - transferred.tainted_amount
    )
    
    # KOS-020: Persist escrow state to Redis (survives restarts and multi-worker)
    import json
    escrow_data = {
        "contract_id": req.contract_id,
        "sender": req.sender,
        "receiver": req.receiver,
        "locked_clean": str(transferred.clean_amount),
        "locked_tainted": str(transferred.tainted_amount),
        "total_locked": str(amount),
        "status": "active"
    }
    r.set(escrow_key, json.dumps(escrow_data))
    
    ledger.set_taint(req.sender, new_sender)

    return {"status": "escrow_created", "contract_id": req.contract_id, "locked_amount": str(amount)}


@app.post("/escrow/settle", dependencies=[Depends(require_api_key)])
def settle_escrow(req: EscrowSettle, ledger: TaintLedger = Depends(get_ledger), r: redis.Redis = Depends(get_redis)):
    """Resolves an escrow, enforces invariants, and distributes UTXOs."""
    import json
    escrow_key = f"kap:escrow:{req.contract_id}"
    
    # KOS-020: Load escrow from Redis instead of in-memory dict
    raw = r.get(escrow_key)
    if not raw:
        raise HTTPException(status_code=404, detail="Escrow not found")
    
    escrow_data = json.loads(raw)
    if escrow_data.get("status") != "active":
        raise HTTPException(status_code=400, detail="Escrow is not active")
    
    ctx = ExecutionContext(ledger)
    payouts_decimal = {k: Decimal(v) for k, v in req.payouts.items()}
    
    # Validate total payouts don't exceed locked amount
    total_payout = sum(payouts_decimal.values())
    locked = Decimal(escrow_data["total_locked"])
    if total_payout > locked:
        raise HTTPException(status_code=400, detail="Payouts exceed locked amount")
    
    try:
        # Distribute funds to receivers
        for agent_id, payout_amount in payouts_decimal.items():
            _validate_agent_id(agent_id)
            current = ledger.get_taint(agent_id)
            # Proportional taint from locked funds
            locked_clean = Decimal(escrow_data["locked_clean"])
            locked_tainted = Decimal(escrow_data["locked_tainted"])
            clean_ratio = locked_clean / locked if locked > 0 else Decimal("1")
            new_asset = TaintedAsset(
                current.clean_amount + (payout_amount * clean_ratio),
                current.tainted_amount + (payout_amount * (1 - clean_ratio))
            )
            ledger.set_taint(agent_id, new_asset)
        
        # Mark escrow as settled in Redis
        escrow_data["status"] = "settled"
        r.set(escrow_key, json.dumps(escrow_data))
        # Set TTL for cleanup (keep for audit trail, 30 days)
        r.expire(escrow_key, 30 * 24 * 3600)
        
        return {"status": "settled", "contract_id": req.contract_id}
        
    except Exception as e:
        raise HTTPException(status_code=403, detail=f"Compliance/Execution Error: {str(e)}")


@app.get("/compliance/verify/{agent_id}", dependencies=[Depends(require_api_key)])
def verify_compliance(agent_id: str, ledger: TaintLedger = Depends(get_ledger)):
    """Returns the agent's compliance status and taint mass."""
    _validate_agent_id(agent_id)
    asset = ledger.get_taint(agent_id)
    ratio = asset.taint_ratio
    
    status = "clean"
    if ratio >= Decimal("0.20"):
        status = "blocked_tainted"
    elif ratio >= Decimal("0.05"):
        status = "warning_tainted"
        
    return {
        "agent_id": agent_id,
        "status": status,
        "taint_ratio": str(ratio),
        "clean_mass": str(asset.clean_amount),
        "tainted_mass": str(asset.tainted_amount),
        "total_mass": str(asset.total_amount)
    }

@app.get("/balance/{agent_id}", dependencies=[Depends(require_api_key)])
def get_balance(agent_id: str, ledger: TaintLedger = Depends(get_ledger)):
    _validate_agent_id(agent_id)
    asset = ledger.get_taint(agent_id)
    return {"agent_id": agent_id, "balance": str(asset.total_amount)}
