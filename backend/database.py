import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from backend.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db(retries: int = 10, delay: float = 2.0):
    """Create all tables, retrying until postgres is ready (handles slow container startup)."""
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ Database tables ready")
            return
        except Exception as e:
            if attempt == retries:
                raise
            logger.warning(f"⏳ DB not ready (attempt {attempt}/{retries}): {e}. Retrying in {delay}s…")
            await asyncio.sleep(delay)
