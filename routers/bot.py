"""
Bot control (start/stop/config) and the internal endpoint the bot engine
calls to fetch per-user config.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os
import uuid

from database import get_db
from models import User, BotConfig, ExchangeKeys, Subscription
from encryption import decrypt
from routers.users import get_current_user

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Secret token the bot engine uses to authenticate internal requests.
# Set BOT_ENGINE_SECRET in Railway env vars — same value on both services.
BOT_ENGINE_SECRET = os.environ.get("BOT_ENGINE_SECRET", "")


def _require_bot_engine(x_bot_secret: str = Header(...)):
    if not BOT_ENGINE_SECRET or x_bot_secret != BOT_ENGINE_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Schemas ───────────────────────────────────────────────────────────────────

class BotConfigUpdate(BaseModel):
    capital_amount: Optional[float] = None

class EquityUpdate(BaseModel):
    equity: float
    hwm:    float


# ── User-facing routes ────────────────────────────────────────────────────────

@router.post("/start")
def start_bot(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = current_user.subscription
    if not sub or sub.status != "active":
        raise HTTPException(status_code=402, detail="Active subscription required")

    keys = db.query(ExchangeKeys).filter(
        ExchangeKeys.user_id == current_user.id
    ).first()
    if not keys or not keys.verified:
        raise HTTPException(status_code=400, detail="Connect and verify your WEEX keys first")

    config = current_user.bot_config
    if not config:
        raise HTTPException(status_code=400, detail="Bot config not found")

    config.is_active = True
    db.commit()
    return {"status": "started"}


@router.post("/stop")
def stop_bot(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = current_user.bot_config
    if config:
        config.is_active = False
        db.commit()
    return {"status": "stopped"}


@router.put("/config")
def update_config(
    body: BotConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = current_user.bot_config
    if not config:
        raise HTTPException(status_code=404, detail="Bot config not found")

    if body.capital_amount is not None:
        if body.capital_amount < 10:
            raise HTTPException(status_code=400, detail="Minimum capital is $10")
        config.capital_amount = body.capital_amount

    db.commit()
    return {"status": "updated", "capital_amount": config.capital_amount}


@router.post("/activate-beta", dependencies=[Depends(_require_bot_engine)])
def activate_beta(user_id: str, db: Session = Depends(get_db)):
    """Manually activate a user's subscription for beta testing."""
    sub = db.query(Subscription).filter(
        Subscription.user_id == uuid.UUID(user_id)
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    sub.status = "active"
    sub.plan   = "pro"
    db.commit()
    return {"status": "activated", "user_id": user_id}


@router.get("/status")
def bot_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = current_user.bot_config
    return {
        "is_active":    config.is_active     if config else False,
        "capital":      config.capital_amount if config else None,
        "equity":       config.equity         if config else None,
        "hwm":          config.hwm            if config else None,
    }


# ── Internal routes (called by the bot engine, not the user) ─────────────────

@router.get("/internal/active-users", dependencies=[Depends(_require_bot_engine)])
def get_active_users(db: Session = Depends(get_db)):
    """
    Returns all users with an active bot + valid subscription.
    Called by the bot engine at the start of every trading cycle.
    """
    configs = (
        db.query(BotConfig)
        .join(User, BotConfig.user_id == User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(
            BotConfig.is_active == True,
            Subscription.status == "active",
        )
        .all()
    )
    return [{"user_id": str(c.user_id), "capital": c.capital_amount} for c in configs]


@router.get("/internal/user-config/{user_id}", dependencies=[Depends(_require_bot_engine)])
def get_user_config(user_id: str, db: Session = Depends(get_db)):
    """
    Returns everything the bot needs to trade for one user:
    decrypted API keys + capital amount.
    """
    uid = uuid.UUID(user_id)

    config = db.query(BotConfig).filter(BotConfig.user_id == uid).first()
    keys   = db.query(ExchangeKeys).filter(ExchangeKeys.user_id == uid).first()

    if not config or not keys:
        raise HTTPException(status_code=404, detail="User config not found")

    return {
        "user_id":    user_id,
        "capital":    config.capital_amount,
        "api_key":    decrypt(keys.api_key_encrypted),
        "api_secret": decrypt(keys.api_secret_encrypted),
        "passphrase": decrypt(keys.passphrase_encrypted) if keys.passphrase_encrypted else "",
    }


@router.post("/internal/equity/{user_id}", dependencies=[Depends(_require_bot_engine)])
def update_equity(user_id: str, body: EquityUpdate, db: Session = Depends(get_db)):
    """Called by the bot engine after every trade to keep equity/HWM in sync."""
    config = db.query(BotConfig).filter(
        BotConfig.user_id == uuid.UUID(user_id)
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    config.equity = body.equity
    config.hwm    = body.hwm
    db.commit()
    return {"status": "ok"}
