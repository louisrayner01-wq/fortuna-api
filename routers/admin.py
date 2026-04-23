"""
Admin routes — only accessible to users with is_admin = True.
Your account gets flagged as admin via the ADMIN_EMAIL env var on first login.
"""

import os
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import User, Subscription, BotConfig, Trade
from routers.users import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")


def get_admin_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    # Auto-promote the admin email on first access
    if ADMIN_EMAIL and current_user.email == ADMIN_EMAIL and not current_user.is_admin:
        current_user.is_admin = True
        db.commit()
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ── Stats overview ────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    total_users   = db.query(User).count()
    active_subs   = db.query(Subscription).filter(Subscription.status == "active").count()
    bots_running  = db.query(BotConfig).filter(BotConfig.is_active == True).count()
    total_trades  = db.query(Trade).count()
    total_pnl     = db.query(func.sum(Trade.pnl_usdt)).scalar() or 0.0

    return {
        "total_users":  total_users,
        "active_subs":  active_subs,
        "bots_running": bots_running,
        "total_trades": total_trades,
        "total_pnl":    round(float(total_pnl), 2),
    }


# ── User list ─────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        sub    = u.subscription
        config = u.bot_config
        trades = db.query(Trade).filter(Trade.user_id == u.id).all()
        total_pnl = sum(t.pnl_usdt for t in trades if t.pnl_usdt is not None)

        result.append({
            "id":          str(u.id),
            "email":       u.email,
            "created_at":  u.created_at,
            "is_admin":    u.is_admin,
            "subscription": {
                "status": sub.status if sub else "none",
                "plan":   sub.plan   if sub else None,
            },
            "bot": {
                "is_active": config.is_active     if config else False,
                "capital":   config.capital_amount if config else None,
                "equity":    config.equity         if config else None,
            },
            "total_trades": len(trades),
            "total_pnl":    round(total_pnl, 2),
        })
    return result


# ── Grant / revoke free access ────────────────────────────────────────────────

@router.post("/grant-by-email")
def grant_by_email(
    body: dict,
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    email = body.get("email", "").strip().lower()
    user  = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"No account found for {email}")
    sub = user.subscription
    if not sub:
        raise HTTPException(status_code=404, detail="User has no subscription record")
    sub.status = "active"
    sub.plan   = "pro"
    db.commit()
    return {"status": "access granted", "email": email, "user_id": str(user.id)}


@router.post("/users/{user_id}/grant-access")
def grant_access(
    user_id: str,
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(
        Subscription.user_id == uuid.UUID(user_id)
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="User not found")
    sub.status = "active"
    sub.plan   = "pro"
    db.commit()
    return {"status": "access granted", "user_id": user_id}


@router.post("/users/{user_id}/revoke-access")
def revoke_access(
    user_id: str,
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    sub = db.query(Subscription).filter(
        Subscription.user_id == uuid.UUID(user_id)
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="User not found")
    sub.status = "inactive"
    db.commit()
    return {"status": "access revoked", "user_id": user_id}
