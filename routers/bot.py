"""
Bot control (start/stop/config) and the internal endpoints the bot engine
calls to fetch per-user config.

All user-facing routes are scoped by `strategy_family`, letting a user run
Strat 1 and Portfolio Strategy independently on the same account (each has
its own capital, equity, HWM, is_active flag, trades log).
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import os
import uuid

from database import get_db
from models import User, BotConfig, ExchangeKeys, Subscription, STRATEGY_FAMILIES
from encryption import decrypt
from routers.users import get_current_user

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Secret token the bot engine uses to authenticate internal requests.
# Set BOT_ENGINE_SECRET in Railway env vars — same value on both services.
BOT_ENGINE_SECRET = os.environ.get("BOT_ENGINE_SECRET", "")


def _require_bot_engine(x_bot_secret: str = Header(...)):
    if not BOT_ENGINE_SECRET or x_bot_secret != BOT_ENGINE_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Family helpers ────────────────────────────────────────────────────────────

VALID_STRATEGY_MODES = {"conservative", "balanced", "aggressive"}
MIN_RISK_PER_TRADE   = 0.005
MAX_RISK_PER_TRADE   = 0.02


def _validate_family(family: str) -> str:
    fam = (family or "strat_1").strip().lower()
    if fam not in STRATEGY_FAMILIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy family. Choose from: {', '.join(STRATEGY_FAMILIES)}",
        )
    return fam


def _get_or_create_config(user: User, family: str, db: Session) -> BotConfig:
    """Return the BotConfig row for (user, family), creating it lazily."""
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user.id, BotConfig.strategy_family == family)
        .first()
    )
    if cfg:
        return cfg
    cfg = BotConfig(user_id=user.id, strategy_family=family)
    db.add(cfg)
    db.flush()
    return cfg


# ── Schemas ───────────────────────────────────────────────────────────────────

class BotConfigUpdate(BaseModel):
    strategy_family: str = "strat_1"
    capital_amount:  Optional[float] = None
    strategy_mode:   Optional[str]   = None
    risk_per_trade:  Optional[float] = None


class FamilyBody(BaseModel):
    strategy_family: str = "strat_1"


class EquityUpdate(BaseModel):
    equity: float
    hwm:    float


# ── User-facing routes ────────────────────────────────────────────────────────

@router.post("/start")
def start_bot(
    body: FamilyBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    family = _validate_family(body.strategy_family)

    sub = current_user.subscription
    if not sub or sub.status != "active":
        raise HTTPException(status_code=402, detail="Active subscription required")

    keys = db.query(ExchangeKeys).filter(
        ExchangeKeys.user_id == current_user.id
    ).first()
    if not keys or not keys.verified:
        raise HTTPException(status_code=400, detail="Connect and verify your WEEX keys first")

    cfg = _get_or_create_config(current_user, family, db)
    cfg.is_active = True
    db.commit()
    return {"status": "started", "strategy_family": family}


@router.post("/stop")
def stop_bot(
    body: FamilyBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    family = _validate_family(body.strategy_family)
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current_user.id, BotConfig.strategy_family == family)
        .first()
    )
    if cfg:
        cfg.is_active = False
        db.commit()
    return {"status": "stopped", "strategy_family": family}


@router.put("/config")
def update_config(
    body: BotConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    family = _validate_family(body.strategy_family)
    cfg = _get_or_create_config(current_user, family, db)

    if body.capital_amount is not None:
        if body.capital_amount < 10:
            raise HTTPException(status_code=400, detail="Minimum capital is $10")
        cfg.capital_amount = body.capital_amount

    if body.strategy_mode is not None:
        # strategy_mode only applies to strat_1; ignore silently for portfolio.
        if family == "strat_1":
            mode = body.strategy_mode.strip().lower()
            if mode not in VALID_STRATEGY_MODES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid strategy. Choose from: {', '.join(sorted(VALID_STRATEGY_MODES))}",
                )
            cfg.strategy_mode = mode

    if body.risk_per_trade is not None:
        if not (MIN_RISK_PER_TRADE <= body.risk_per_trade <= MAX_RISK_PER_TRADE):
            raise HTTPException(
                status_code=400,
                detail=(f"Risk per trade must be between "
                        f"{MIN_RISK_PER_TRADE*100:.1f}% and {MAX_RISK_PER_TRADE*100:.1f}%"),
            )
        cfg.risk_per_trade = body.risk_per_trade

    db.commit()
    return {
        "status":          "updated",
        "strategy_family": family,
        "capital_amount":  cfg.capital_amount,
        "strategy_mode":   cfg.strategy_mode,
        "risk_per_trade":  cfg.risk_per_trade,
    }


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
    strategy_family: str = Query("strat_1"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    family = _validate_family(strategy_family)
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == current_user.id, BotConfig.strategy_family == family)
        .first()
    )
    return {
        "strategy_family": family,
        "is_active":       cfg.is_active           if cfg else False,
        "capital":         cfg.capital_amount      if cfg else None,
        "equity":          cfg.equity              if cfg else None,
        "hwm":             cfg.hwm                 if cfg else None,
        "strategy_mode":   cfg.strategy_mode       if cfg else "conservative",
        "risk_per_trade":  cfg.risk_per_trade      if cfg else 0.01,
    }


# ── Internal routes (called by the bot engine, not the user) ─────────────────

@router.get("/internal/active-users", dependencies=[Depends(_require_bot_engine)])
def get_active_users(
    strategy_family: str = Query("strat_1"),
    db: Session = Depends(get_db),
):
    """
    Returns all users with an active bot in the given family + valid subscription.
    Called by the bot engine at the start of every trading cycle. Each family
    (strat_1, portfolio) polls its own worker.
    """
    family = _validate_family(strategy_family)
    configs = (
        db.query(BotConfig)
        .join(User, BotConfig.user_id == User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(
            BotConfig.strategy_family == family,
            BotConfig.is_active == True,
            Subscription.status == "active",
        )
        .all()
    )
    return [{
        "user_id":         str(c.user_id),
        "strategy_family": c.strategy_family,
        "capital":         c.capital_amount,
        "strategy_mode":   c.strategy_mode  or "conservative",
        "risk_per_trade":  c.risk_per_trade if c.risk_per_trade is not None else 0.01,
    } for c in configs]


@router.get("/internal/user-config/{user_id}", dependencies=[Depends(_require_bot_engine)])
def get_user_config(
    user_id: str,
    strategy_family: str = Query("strat_1"),
    db: Session = Depends(get_db),
):
    """
    Returns everything the bot needs to trade for one user + family:
    decrypted API keys + capital + risk settings.
    """
    family = _validate_family(strategy_family)
    uid = uuid.UUID(user_id)

    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == uid, BotConfig.strategy_family == family)
        .first()
    )
    keys = db.query(ExchangeKeys).filter(ExchangeKeys.user_id == uid).first()

    if not cfg or not keys:
        raise HTTPException(status_code=404, detail="User config not found")

    return {
        "user_id":         user_id,
        "strategy_family": family,
        "capital":         cfg.capital_amount,
        "api_key":         decrypt(keys.api_key_encrypted),
        "api_secret":      decrypt(keys.api_secret_encrypted),
        "passphrase":      decrypt(keys.passphrase_encrypted) if keys.passphrase_encrypted else "",
        "strategy_mode":   cfg.strategy_mode  or "conservative",
        "risk_per_trade":  cfg.risk_per_trade if cfg.risk_per_trade is not None else 0.01,
    }


@router.post("/internal/equity/{user_id}", dependencies=[Depends(_require_bot_engine)])
def update_equity(
    user_id: str,
    body: EquityUpdate,
    strategy_family: str = Query("strat_1"),
    db: Session = Depends(get_db),
):
    """Called by the bot engine after every trade to keep equity/HWM in sync."""
    family = _validate_family(strategy_family)
    cfg = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == uuid.UUID(user_id), BotConfig.strategy_family == family)
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    cfg.equity = body.equity
    cfg.hwm    = body.hwm
    db.commit()
    return {"status": "ok", "strategy_family": family}
