from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from routers import users, exchange, bot, trades, admin, payments
from sqlalchemy import text

# Create all tables on startup
Base.metadata.create_all(bind=engine)

# Add any missing columns that were added after initial deployment
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE"))
    conn.commit()

app = FastAPI(title="Fortuna API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this once the frontend URL is known
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(exchange.router)
app.include_router(bot.router)
app.include_router(trades.router)
app.include_router(admin.router)
app.include_router(payments.router)


@app.get("/health")
def health():
    return {"status": "ok"}
