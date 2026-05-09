"""
User registration, login, and profile.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from jose import jwt, JWTError
import uuid

from database import get_db
from models import User, Subscription, BotConfig, Affiliate, AffiliateReferral
from auth import hash_password, verify_password, create_token, decode_token, SECRET_KEY, ALGORITHM

WEB_URL        = os.environ.get("WEB_URL", "https://fortuna-web-one.vercel.app")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("RESEND_FROM_EMAIL", "noreply@fortuna.app")

router  = APIRouter(prefix="/api/users", tags=["users"])
bearer  = HTTPBearer()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name:     str
    email:    EmailStr
    password: str
    ref_code: str = ""

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token:    str
    password: str

class TokenResponse(BaseModel):
    token:   str
    user_id: str


# ── Dependency: current user from JWT ────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    ref_code = body.ref_code.strip().upper() if body.ref_code else None
    user = User(
        name             = body.name.strip(),
        email            = body.email,
        password_hash    = hash_password(body.password),
        referred_by_code = ref_code,
    )
    db.add(user)
    db.flush()

    # Create empty subscription + bot config rows for this user
    db.add(Subscription(user_id=user.id))
    db.add(BotConfig(user_id=user.id))

    # Record affiliate referral if a valid active affiliate code was used
    if ref_code:
        aff = db.query(Affiliate).filter(
            Affiliate.code == ref_code,
            Affiliate.status == "active",
        ).first()
        if aff:
            db.add(AffiliateReferral(affiliate_id=aff.id, user_id=user.id))

    db.commit()
    db.refresh(user)

    return TokenResponse(token=create_token(str(user.id)), user_id=str(user.id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(token=create_token(str(user.id)), user_id=str(user.id))


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = current_user.bot_config
    sub    = current_user.subscription
    return {
        "id":             str(current_user.id),
        "name":           current_user.name,
        "email":          current_user.email,
        "created_at":     current_user.created_at,
        "subscription": {
            "status": sub.status if sub else "inactive",
            "plan":   sub.plan   if sub else None,
        },
        "bot": {
            "is_active":     config.is_active     if config else False,
            "capital":       config.capital_amount if config else None,
            "equity":        config.equity         if config else None,
            "hwm":           config.hwm            if config else None,
        },
    }


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if user:
        expire = datetime.now(timezone.utc) + timedelta(hours=1)
        token  = jwt.encode(
            {"sub": str(user.id), "purpose": "reset", "exp": expire},
            SECRET_KEY, algorithm=ALGORITHM,
        )
        reset_url = f"{WEB_URL}/reset-password?token={token}"

        if RESEND_API_KEY:
            try:
                import resend
                resend.api_key = RESEND_API_KEY
                resend.Emails.send({
                    "from":    FROM_EMAIL,
                    "to":      [user.email],
                    "subject": "Reset your Fortuna password",
                    "html":    f"""
                        <p>Hi,</p>
                        <p>Click the link below to reset your password. This link expires in 1 hour.</p>
                        <p><a href="{reset_url}">{reset_url}</a></p>
                        <p>If you didn't request this, ignore this email.</p>
                    """,
                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("Email send failed: %s — RESET LINK: %s", e, reset_url)
        else:
            import logging
            logging.getLogger(__name__).info("RESET LINK (no email provider): %s", reset_url)

    # Always return success so we don't leak whether an email exists
    return {"message": "If that email is registered you'll receive a reset link shortly."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(body.token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "reset":
            raise ValueError
        user_id = payload.get("sub")
    except (JWTError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user.password_hash = hash_password(body.password)
    db.commit()
    return {"message": "Password updated successfully"}
