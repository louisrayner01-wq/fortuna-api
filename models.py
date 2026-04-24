from sqlalchemy import Column, String, Float, Boolean, Integer, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from database import Base



class User(Base):
    __tablename__ = "users"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email              = Column(String, unique=True, nullable=False, index=True)
    password_hash      = Column(String, nullable=False)
    stripe_customer_id = Column(String, nullable=True)
    is_active          = Column(Boolean, default=True)
    is_admin           = Column(Boolean, default=False)
    referred_by_code   = Column(String, nullable=True)   # affiliate code used at signup
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    subscription  = relationship("Subscription",  back_populates="user", uselist=False)
    bot_config    = relationship("BotConfig",      back_populates="user", uselist=False)
    exchange_keys = relationship("ExchangeKeys",   back_populates="user", uselist=False)
    trades        = relationship("Trade",          back_populates="user")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    stripe_sub_id       = Column(String, nullable=True)
    status              = Column(String, default="inactive")   # active | inactive | cancelled
    plan                = Column(String, default="starter")    # starter | pro
    current_period_end  = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="subscription")


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    capital_amount  = Column(Float, default=100.0)
    is_active       = Column(Boolean, default=False)
    equity          = Column(Float, nullable=True)        # live equity, updated by bot
    hwm             = Column(Float, nullable=True)        # high-water mark, updated by bot
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User", back_populates="bot_config")


class ExchangeKeys(Base):
    __tablename__ = "exchange_keys"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    exchange            = Column(String, default="weex")
    api_key_encrypted   = Column(Text, nullable=False)
    api_secret_encrypted = Column(Text, nullable=False)
    passphrase_encrypted = Column(Text, nullable=True)
    verified            = Column(Boolean, default=False)   # True once balance check passes
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="exchange_keys")


class Trade(Base):
    __tablename__ = "trades"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    pair         = Column(String, nullable=False)
    slot_key     = Column(String, nullable=True)
    side         = Column(String, nullable=False)        # long | short
    entry_price  = Column(Float, nullable=False)
    exit_price   = Column(Float, nullable=True)
    quantity     = Column(Float, nullable=False)
    leverage     = Column(Integer, default=1)
    confidence   = Column(Float, nullable=True)
    pnl_pct      = Column(Float, nullable=True)
    pnl_usdt     = Column(Float, nullable=True)
    candles_held = Column(Integer, nullable=True)
    exit_reason  = Column(String, nullable=True)         # stop_loss | take_profit | tp1_partial | signal
    equity_after = Column(Float, nullable=True)
    mae_pct      = Column(Float, nullable=True)
    mfe_pct      = Column(Float, nullable=True)
    wick_breach  = Column(Integer, default=0)
    opened_at    = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="trades")


# ── Affiliate tables ───────────────────────────────────────────────────────────

class Affiliate(Base):
    __tablename__ = "affiliates"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name            = Column(String, nullable=False)
    email           = Column(String, unique=True, nullable=False, index=True)
    password_hash   = Column(String, nullable=False)
    code            = Column(String, unique=True, nullable=False, index=True)  # e.g. "JOHN42"
    commission_rate = Column(Float, default=0.20)   # 20 % of each payment
    payout_email    = Column(String, nullable=True)  # PayPal / bank details for payouts
    status          = Column(String, default="pending")  # pending | active | suspended
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    referrals = relationship("AffiliateReferral", back_populates="affiliate")
    earnings  = relationship("AffiliateEarning",  back_populates="affiliate")


class AffiliateReferral(Base):
    """One row per user who signed up via an affiliate link."""
    __tablename__ = "affiliate_referrals"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    affiliate_id = Column(UUID(as_uuid=True), ForeignKey("affiliates.id"), nullable=False)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"),      nullable=False)
    converted    = Column(Boolean, default=False)  # True once they make their first payment
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    affiliate = relationship("Affiliate", back_populates="referrals")
    user      = relationship("User")
    earnings  = relationship("AffiliateEarning", back_populates="referral")


class AffiliateEarning(Base):
    """One row per Stripe payment made by a referred user."""
    __tablename__ = "affiliate_earnings"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    affiliate_id      = Column(UUID(as_uuid=True), ForeignKey("affiliates.id"),        nullable=False)
    referral_id       = Column(UUID(as_uuid=True), ForeignKey("affiliate_referrals.id"), nullable=False)
    stripe_invoice_id = Column(String, nullable=True, unique=True)
    amount_gbp        = Column(Float, nullable=False)   # what the user paid (£)
    commission_gbp    = Column(Float, nullable=False)   # affiliate's cut (£)
    status            = Column(String, default="pending")  # pending | paid
    created_at        = Column(DateTime(timezone=True), server_default=func.now())

    affiliate = relationship("Affiliate", back_populates="earnings")
    referral  = relationship("AffiliateReferral", back_populates="earnings")
