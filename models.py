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
