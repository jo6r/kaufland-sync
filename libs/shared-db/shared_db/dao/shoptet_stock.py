"""DAO for shoptet_stock operations."""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from shared_db.models import ShoptetStock


def bulk_upsert_stock(session: Session, items: List[Dict[str, Any]]) -> None:
    """
    Bulk upsert stock items.
    
    For each item:
    - If code exists: update ean, name, product_visibility, ingested_at always
    - If qty changed: update qty and changed_at
    - If code doesn't exist: insert new record
    
    Args:
        session: SQLAlchemy session
        items: List of dicts with keys: ean, code, name, qty, product_visibility
    """
    if not items:
        return
    
    for item in items:
        ean = item.get('ean')
        code = item.get('code')
        name = item.get('name')
        qty = item.get('qty')
        product_visibility = item.get('product_visibility')
        
        # Try to get existing record by code
        existing = session.query(ShoptetStock).filter(ShoptetStock.code == code).first()
        
        if existing:
            # Update existing record
            qty_changed = existing.qty != qty
            
            existing.ean = ean
            existing.name = name
            existing.product_visibility = product_visibility
            existing.ingested_at = datetime.now(timezone.utc)
            
            if qty_changed:
                existing.qty = qty
                existing.changed_at = datetime.now(timezone.utc)
        else:
            # Insert new record
            new_stock = ShoptetStock(
                ean=ean,
                code=code,
                name=name,
                qty=qty,
                product_visibility=product_visibility,
                changed_at=datetime.now(timezone.utc),
                ingested_at=datetime.now(timezone.utc),
            )
            session.add(new_stock)


def get_stock_changed_after(
    session: Session,
    since_dt: datetime,
    limit: Optional[int] = None
) -> List[ShoptetStock]:
    """
    Get stock items changed after specified datetime.
    
    Args:
        session: SQLAlchemy session
        since_dt: Datetime to filter by (changed_at > since_dt)
        limit: Optional limit on number of results
        
    Returns:
        List of ShoptetStock objects ordered by changed_at
    """
    query = session.query(ShoptetStock).filter(
        ShoptetStock.changed_at > since_dt
    ).order_by(ShoptetStock.changed_at)
    
    if limit is not None:
        query = query.limit(limit)
    
    return query.all()


def get_all_eans(session: Session) -> List[str]:
    """
    Get all EANs from shoptet_stock.
    
    Args:
        session: SQLAlchemy session
        
    Returns:
        List of EAN strings
    """
    results = session.query(ShoptetStock.ean).all()
    return [row[0] for row in results]