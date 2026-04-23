"""
Trade reporting — bot engine posts trades here, dashboard reads them.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid

from database import get_db
from models import Trade, User
from routers.users import get_current_user

router = APIRouter(prefix="/api/trades", tags=["trades"])

BOT_ENGINE_SECRET = os.environ.get("BOT_ENGINE_SECRET", "")


def _require_bot_engine(x_bot_secret: str = Header(...)):
    if not BOT_ENGINE_SECRET or x_bot_secret != BOT_ENGINE_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Schema ────────────────────────────────────────────────────────────────────

class TradeIn(BaseModel):
    user_id:      str
    pair:         str
    slot_key:     Optional[str]  = None
    side:         str
    entry_price:  float
    exit_price:   Optional[float] = None
    quantity:     float
    leverage:     int             = 1
    confidence:   Optional[float] = None
    pnl_pct:      Optional[float] = None
    pnl_usdt:     Optional[float] = None
    candles_held: Optional[int]   = None
    exit_reason:  Optional[str]   = None
    equity_after: Optional[float] = None
    mae_pct:      Optional[float] = None
    mfe_pct:      Optional[float] = None
    wick_breach:  int             = 0


# ── Internal: bot posts trades ────────────────────────────────────────────────

@router.post("/internal", dependencies=[Depends(_require_bot_engine)], status_code=201)
def record_trade(body: TradeIn, db: Session = Depends(get_db)):
    trade = Trade(
        user_id      = uuid.UUID(body.user_id),
        pair         = body.pair,
        slot_key     = body.slot_key,
        side         = body.side,
        entry_price  = body.entry_price,
        exit_price   = body.exit_price,
        quantity     = body.quantity,
        leverage     = body.leverage,
        confidence   = body.confidence,
        pnl_pct      = body.pnl_pct,
        pnl_usdt     = body.pnl_usdt,
        candles_held = body.candles_held,
        exit_reason  = body.exit_reason,
        equity_after = body.equity_after,
        mae_pct      = body.mae_pct,
        mfe_pct      = body.mfe_pct,
        wick_breach  = body.wick_breach,
    )
    db.add(trade)
    db.commit()
    return {"status": "recorded", "trade_id": str(trade.id)}


# ── User-facing: dashboard reads trades ──────────────────────────────────────

@router.get("")
def get_my_trades(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trades = (
        db.query(Trade)
        .filter(Trade.user_id == current_user.id)
        .order_by(Trade.opened_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":           str(t.id),
            "pair":         t.pair,
            "side":         t.side,
            "entry_price":  t.entry_price,
            "exit_price":   t.exit_price,
            "pnl_usdt":     t.pnl_usdt,
            "pnl_pct":      t.pnl_pct,
            "exit_reason":  t.exit_reason,
            "equity_after": t.equity_after,
            "opened_at":    t.opened_at,
        }
        for t in trades
    ]


@router.get("/pnl-chart")
def get_pnl_chart(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from collections import defaultdict
    from datetime import datetime, timezone

    trades = (
        db.query(Trade)
        .filter(Trade.user_id == current_user.id, Trade.pnl_usdt.isnot(None))
        .order_by(Trade.opened_at.asc())
        .all()
    )

    daily   = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    weekly  = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    equity_series = []

    for t in trades:
        ts = t.opened_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        day_key   = ts.strftime("%Y-%m-%d")
        # ISO week: e.g. "2025-W22"
        week_key  = ts.strftime("%Y-W%W")
        month_key = ts.strftime("%Y-%m")

        daily[day_key]["pnl"]    += t.pnl_usdt
        daily[day_key]["trades"] += 1

        weekly[week_key]["pnl"]    += t.pnl_usdt
        weekly[week_key]["trades"] += 1

        monthly[month_key]["pnl"]    += t.pnl_usdt
        monthly[month_key]["trades"] += 1

        if t.equity_after is not None:
            equity_series.append({"date": day_key, "equity": round(t.equity_after, 2)})

    def sorted_list(d, key_field="date"):
        return [
            {key_field: k, "pnl": round(v["pnl"], 2), "trades": v["trades"]}
            for k, v in sorted(d.items())
        ]

    return {
        "daily":         sorted_list(daily),
        "weekly":        sorted_list(weekly,  key_field="week"),
        "monthly":       sorted_list(monthly, key_field="month"),
        "equity_series": equity_series,
    }


@router.get("/summary")
def get_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trades = db.query(Trade).filter(
        Trade.user_id == current_user.id,
        Trade.pnl_usdt.isnot(None),
    ).all()

    if not trades:
        return {"total_trades": 0, "total_pnl": 0, "win_rate": 0}

    wins      = [t for t in trades if t.pnl_usdt > 0]
    total_pnl = sum(t.pnl_usdt for t in trades)
    win_rate  = round(len(wins) / len(trades) * 100, 1)

    return {
        "total_trades": len(trades),
        "total_pnl":    round(total_pnl, 2),
        "win_rate":     win_rate,
        "avg_win":      round(sum(t.pnl_usdt for t in wins) / max(len(wins), 1), 2),
        "avg_loss":     round(sum(t.pnl_usdt for t in trades if t.pnl_usdt <= 0) / max(len(trades) - len(wins), 1), 2),
    }
