"""DAO for shoptet_unit_mapping operations."""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from shared_db.models import ShoptetUnitMapping


def bulk_upsert_mapping(
    session: Session,
    items: List[Dict[str, Any]],
    fetched_at: Optional[datetime] = None
) -> None:
    """
    Bulk upsert mapping items.

    Args:
        session: SQLAlchemy session
        items: List of dicts with keys: ean, id_unit, status (optional)
        fetched_at: Optional datetime for last_fetch_at field
    """
    if not items:
        return

    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)

    for item in items:
        ean = item.get('ean')
        id_unit = item.get('id_unit')
        status = item.get('status')

        if not id_unit:
            continue

        # Try to get existing record by ean or id_unit
        existing = session.query(ShoptetUnitMapping).filter(
            or_(
                ShoptetUnitMapping.ean == ean,
                ShoptetUnitMapping.id_unit == id_unit
            )
        ).first()

        if existing:
            # Update existing record
            existing.ean = ean
            existing.id_unit = id_unit
            existing.status = status
            existing.last_fetch_at = fetched_at
        else:
            # Insert new record
            new_mapping = ShoptetUnitMapping(
                ean=ean,
                id_unit=id_unit,
                status=status,
                last_fetch_at=fetched_at,
            )
            session.add(new_mapping)


def get_mapping_by_eans(
    session: Session,
    eans: List[str]
) -> List[ShoptetUnitMapping]:
    """
    Get mappings by EANs.

    Args:
        session: SQLAlchemy session
        eans: List of EANs to look up

    Returns:
        List of ShoptetUnitMapping objects
    """
    if not eans:
        return []

    return session.query(ShoptetUnitMapping).filter(
        ShoptetUnitMapping.ean.in_(eans)
    ).all()
