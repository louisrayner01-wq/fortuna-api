"""
User registration, login, and profile.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
import uuid

from database import get_db
from models import User, Subscription, BotConfig, Affiliate, AffiliateReferral
from auth import hash_password, verify_password, create_token, decode_token

router  = APIRouter(prefix="/api/users", tags=["users"])
bearer  = HTTPBearer()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email:    EmailStr
    password: str
    ref_code: str = ""   # optional affiliate referral code

class LoginRequest(BaseModel):
    email:    EmailStr
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
        email           = body.email,
        password_hash   = hash_password(body.password),
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
