"""DAO for jiri_models_feed_item operations."""

from typing import List, Dict, Any

from sqlalchemy.orm import Session

from shared_db.models import JiriModelsFeedItem


def get_all_jiri_feed_items(session: Session) -> List[Dict[str, Any]]:
    """
    Get all items from jiri_models_feed_item (ean, stock).

    Returns:
        List of dicts with keys: ean, stock
    """
    rows = session.query(JiriModelsFeedItem.ean, JiriModelsFeedItem.stock).all()
    return [{"ean": row[0], "stock": row[1]} for row in rows]
