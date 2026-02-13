"""SQLAlchemy ORM models."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, BigInteger, String, Integer, DateTime, UniqueConstraint, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class ShoptetStock(Base):
    """Model for shoptet_stock table."""
    
    __tablename__ = 'shoptet_stock'
    
    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    ean = Column(String(32), nullable=False, unique=True)
    code = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    qty = Column(Integer, nullable=False, default=0)
    product_visibility = Column(String(32), nullable=True)
    changed_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    ingested_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f"<ShoptetStock(ean='{self.ean}', code='{self.code}', qty={self.qty})>"


class ShoptetUnitMapping(Base):
    """Model for shoptet_unit_mapping table."""

    __tablename__ = 'shoptet_unit_mapping'

    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    ean = Column(String(32), nullable=False, unique=True)
    id_unit = Column(String(64), nullable=False, unique=True)
    status = Column(
        String(64),
        nullable=False,
        default='ACTIVE',
        index=True
    )
    last_fetch_at = Column(DateTime(3), nullable=True)
    updated_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'},
    )

    def __repr__(self):
        return f"<ShoptetUnitMapping(ean='{self.ean}', id_unit='{self.id_unit}')>"


class JobState(Base):
    """Model for job_state table."""

    __tablename__ = 'job_state'

    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    job_name = Column(String(64), nullable=False, unique=True)
    last_run_at = Column(DateTime(3), nullable=True)
    updated_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<JobState(job_name='{self.job_name}', last_run_at={self.last_run_at})>"


class JiriModelsFeedItem(Base):
    """Model for jiri_models_feed_item table (XML feed: CODE, EAN, STOCK)."""

    __tablename__ = 'jiri_models_feed_item'

    id = Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    ean = Column(String(32), nullable=False, unique=True)
    code = Column(String(64), nullable=False, index=True)
    stock = Column(String(32), nullable=False)
    changed_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    ingested_at = Column(DateTime(3), nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<JiriModelsFeedItem(ean='{self.ean}', code='{self.code}', stock='{self.stock}')>"
