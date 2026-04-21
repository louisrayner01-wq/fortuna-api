"""
WEEX API key connection — encrypt and store, verify with a balance check.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import requests

from database import get_db
from models import ExchangeKeys, User
from encryption import encrypt, decrypt
from routers.users import get_current_user

router = APIRouter(prefix="/api/exchange", tags=["exchange"])


class KeysRequest(BaseModel):
    api_key:    str
    api_secret: str
    passphrase: str = ""


@router.post("/connect")
def connect_exchange(
    body: KeysRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Encrypt keys before storing
    existing = db.query(ExchangeKeys).filter(
        ExchangeKeys.user_id == current_user.id
    ).first()

    encrypted_key        = encrypt(body.api_key)
    encrypted_secret     = encrypt(body.api_secret)
    encrypted_passphrase = encrypt(body.passphrase) if body.passphrase else ""

    if existing:
        existing.api_key_encrypted    = encrypted_key
        existing.api_secret_encrypted = encrypted_secret
        existing.passphrase_encrypted = encrypted_passphrase
        existing.verified             = False
        db.commit()
        db.refresh(existing)
        keys_row = existing
    else:
        keys_row = ExchangeKeys(
            user_id              = current_user.id,
            api_key_encrypted    = encrypted_key,
            api_secret_encrypted = encrypted_secret,
            passphrase_encrypted = encrypted_passphrase,
        )
        db.add(keys_row)
        db.commit()
        db.refresh(keys_row)

    # Verify keys with a read-only balance check against WEEX
    verified = _verify_weex_keys(body.api_key, body.api_secret, body.passphrase)
    keys_row.verified = verified
    db.commit()

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="Keys saved but verification failed — check they are correct and have Trade permission enabled"
        )

    return {"status": "connected", "verified": True}


@router.get("/status")
def exchange_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    keys = db.query(ExchangeKeys).filter(
        ExchangeKeys.user_id == current_user.id
    ).first()
    return {
        "connected": keys is not None,
        "verified":  keys.verified if keys else False,
    }


@router.delete("/disconnect")
def disconnect_exchange(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(ExchangeKeys).filter(
        ExchangeKeys.user_id == current_user.id
    ).delete()
    db.commit()
    return {"status": "disconnected"}


def _verify_weex_keys(api_key: str, api_secret: str, passphrase: str) -> bool:
    """
    Attempt a read-only balance fetch from WEEX to verify the keys work.
    Uses the same HMAC-SHA256 + Base64 auth method as the bot's weex_client.py.
    Returns True if the call succeeds, False otherwise.
    """
    try:
        import hmac
        import hashlib
        import base64
        import time

        timestamp = str(int(time.time() * 1000))
        method    = "GET"
        path      = "/api/v2/account/assets"
        message   = timestamp + method + path

        signature = base64.b64encode(
            hmac.new(
                api_secret.encode("utf-8"),
                message.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode()

        headers = {
            "ACCESS-KEY":        api_key,
            "ACCESS-SIGN":       signature,
            "ACCESS-TIMESTAMP":  timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type":      "application/json",
        }
        resp = requests.get(
            "https://api-spot.weex.com" + path,
            headers=headers,
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False
