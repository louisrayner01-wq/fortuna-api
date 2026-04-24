"""
affiliates.py — Affiliate programme endpoints.

Public:
  POST /api/affiliates/register   — apply to be an affiliate
  POST /api/affiliates/login      — affiliate login

Authenticated (affiliate JWT):
  GET  /api/affiliates/me         — profile + stats summary
  GET  /api/affiliates/referrals  — list of referred users
  GET  /api/affiliates/earnings   — earnings history
  PUT  /api/affiliates/payout     — update payout email

Admin (user is_admin):
  GET  /api/affiliates/admin/all          — all affiliates
  POST /api/affiliates/admin/{id}/approve — approve a pending affiliate
  POST /api/affiliates/admin/{id}/mark-paid — mark earnings as paid
"""

import os
import uuid
import random
import string

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import get_db
from models import Affiliate, AffiliateReferral, AffiliateEarning, User
from auth import hash_password, verify_password, create_token, decode_token
from routers.users import get_current_user

router = APIRouter(prefix="/api/affiliates", tags=["affiliates"])
bearer = HTTPBearer()

WEB_URL = os.environ.get("WEB_URL", "https://fortuna-web.vercel.app")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_code(name: str, db: Session) -> str:
    """Generate a unique affiliate code like LOUIS7K."""
    base = "".join(c.upper() for c in name.split()[0] if c.isalpha())[:6]
    for _ in range(20):
        suffix = "".join(random.choices(string.digits, k=3))
        code = f"{base}{suffix}"
        if not db.query(Affiliate).filter(Affiliate.code == code).first():
            return code
    return base + str(uuid.uuid4().hex[:4]).upper()


def _affiliate_token(affiliate_id: str) -> str:
    """Reuse the same JWT helper but prefix the subject so we can tell it apart."""
    return create_token(f"aff:{affiliate_id}")


def _get_affiliate_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Affiliate:
    subject = decode_token(credentials.credentials)
    if not subject or not subject.startswith("aff:"):
        raise HTTPException(status_code=401, detail="Invalid affiliate token")
    aff_id = subject.removeprefix("aff:")
    aff = db.query(Affiliate).filter(Affiliate.id == uuid.UUID(aff_id)).first()
    if not aff:
        raise HTTPException(status_code=401, detail="Affiliate not found")
    return aff


# ── Schemas ───────────────────────────────────────────────────────────────────

class AffiliateRegister(BaseModel):
    name:         str
    email:        EmailStr
    password:     str
    payout_email: Optional[str] = None

class AffiliateLogin(BaseModel):
    email:    EmailStr
    password: str

class PayoutUpdate(BaseModel):
    payout_email: str


# ── Public ────────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def affiliate_register(body: AffiliateRegister, db: Session = Depends(get_db)):
    if db.query(Affiliate).filter(Affiliate.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    code = _gen_code(body.name, db)
    aff  = Affiliate(
        name          = body.name,
        email         = body.email,
        password_hash = hash_password(body.password),
        code          = code,
        payout_email  = body.payout_email,
        status        = "pending",   # admin must approve before link goes live
    )
    db.add(aff)
    db.commit()
    db.refresh(aff)

    return {
        "message": "Application received. You'll be notified once approved.",
        "affiliate_id": str(aff.id),
        "code": aff.code,
        "link": f"{WEB_URL}/register?ref={aff.code}",
    }


@router.post("/login")
def affiliate_login(body: AffiliateLogin, db: Session = Depends(get_db)):
    aff = db.query(Affiliate).filter(Affiliate.email == body.email).first()
    if not aff or not verify_password(body.password, aff.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {
        "token":        _affiliate_token(str(aff.id)),
        "affiliate_id": str(aff.id),
        "status":       aff.status,
    }


# ── Authenticated affiliate ───────────────────────────────────────────────────

@router.get("/me")
def affiliate_me(aff: Affiliate = Depends(_get_affiliate_from_token)):
    total_referrals  = len(aff.referrals)
    total_converted  = sum(1 for r in aff.referrals if r.converted)
    total_earned     = sum(e.commission_gbp for e in aff.earnings)
    total_pending    = sum(e.commission_gbp for e in aff.earnings if e.status == "pending")
    total_paid       = sum(e.commission_gbp for e in aff.earnings if e.status == "paid")

    return {
        "id":              str(aff.id),
        "name":            aff.name,
        "email":           aff.email,
        "code":            aff.code,
        "link":            f"{WEB_URL}/register?ref={aff.code}",
        "commission_rate": aff.commission_rate,
        "payout_email":    aff.payout_email,
        "status":          aff.status,
        "stats": {
            "total_referrals": total_referrals,
            "converted":       total_converted,
            "total_earned_gbp": round(total_earned, 2),
            "pending_gbp":      round(total_pending, 2),
            "paid_gbp":         round(total_paid, 2),
        },
    }


@router.get("/referrals")
def affiliate_referrals(aff: Affiliate = Depends(_get_affiliate_from_token), db: Session = Depends(get_db)):
    rows = []
    for ref in sorted(aff.referrals, key=lambda r: r.created_at, reverse=True):
        user = ref.user
        sub  = user.subscription if user else None
        rows.append({
            "referral_id":  str(ref.id),
            "user_email":   user.email if user else "—",
            "signed_up":    ref.created_at.isoformat(),
            "converted":    ref.converted,
            "sub_status":   sub.status if sub else "none",
            "total_earned": round(sum(e.commission_gbp for e in ref.earnings), 2),
        })
    return rows


@router.get("/earnings")
def affiliate_earnings(aff: Affiliate = Depends(_get_affiliate_from_token)):
    rows = []
    for e in sorted(aff.earnings, key=lambda x: x.created_at, reverse=True):
        rows.append({
            "id":           str(e.id),
            "date":         e.created_at.isoformat(),
            "amount_gbp":   e.amount_gbp,
            "commission_gbp": e.commission_gbp,
            "status":       e.status,
            "invoice_id":   e.stripe_invoice_id,
        })
    return rows


@router.put("/payout")
def update_payout(
    body: PayoutUpdate,
    aff:  Affiliate = Depends(_get_affiliate_from_token),
    db:   Session   = Depends(get_db),
):
    aff.payout_email = body.payout_email
    db.commit()
    return {"message": "Payout email updated"}


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/admin/all")
def admin_all_affiliates(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")

    affs = db.query(Affiliate).order_by(Affiliate.created_at.desc()).all()
    return [
        {
            "id":              str(a.id),
            "name":            a.name,
            "email":           a.email,
            "code":            a.code,
            "status":          a.status,
            "commission_rate": a.commission_rate,
            "payout_email":    a.payout_email,
            "referrals":       len(a.referrals),
            "converted":       sum(1 for r in a.referrals if r.converted),
            "total_earned":    round(sum(e.commission_gbp for e in a.earnings), 2),
            "pending_payout":  round(sum(e.commission_gbp for e in a.earnings if e.status == "pending"), 2),
            "created_at":      a.created_at.isoformat(),
        }
        for a in affs
    ]


@router.post("/admin/{affiliate_id}/approve")
def admin_approve(
    affiliate_id: str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    aff = db.query(Affiliate).filter(Affiliate.id == uuid.UUID(affiliate_id)).first()
    if not aff:
        raise HTTPException(status_code=404, detail="Affiliate not found")
    aff.status = "active"
    db.commit()
    return {"message": f"{aff.name} approved", "code": aff.code}


@router.post("/admin/{affiliate_id}/mark-paid")
def admin_mark_paid(
    affiliate_id: str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    aff = db.query(Affiliate).filter(Affiliate.id == uuid.UUID(affiliate_id)).first()
    if not aff:
        raise HTTPException(status_code=404, detail="Affiliate not found")
    for e in aff.earnings:
        if e.status == "pending":
            e.status = "paid"
    db.commit()
    return {"message": f"All pending earnings for {aff.name} marked as paid"}
