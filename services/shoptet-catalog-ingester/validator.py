"""Validation for Shoptet catalog data."""

import logging
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)


def validate_item(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate catalog item.
    
    Validates that EAN and qty are present and valid.
    
    Args:
        item: Dictionary with keys: ean, code, name, qty
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check EAN
    ean = item.get('ean')
    if not ean:
        return False, "EAN is required"
    
    if not isinstance(ean, str):
        ean = str(ean)
    
    ean = ean.strip()
    if not ean:
        return False, "EAN cannot be empty"
        
    # Check qty
    qty = item.get('qty')
    if qty is None:
        return False, "qty is required"
    
    if not isinstance(qty, int):
        try:
            qty = int(qty)
        except (ValueError, TypeError):
            return False, f"qty must be an integer: {qty}"
    
    # Negative values are handled in csv_parser (changed to 0), so we allow them here
    # They will be corrected before reaching validation
    
    return True, None


def validate_items(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Validate list of items and separate valid from invalid.
    
    Args:
        items: List of item dictionaries
        
    Returns:
        Tuple of (valid_items, invalid_items)
    """
    valid_items = []
    invalid_items = []
    
    for item in items:
        is_valid, error = validate_item(item)
        if is_valid:
            valid_items.append(item)
        else:
            logger.warning(f"Invalid item: {item}, error: {error}")
            invalid_items.append(item)
    
    logger.info(f"Validated {len(items)} items: {len(valid_items)} valid, {len(invalid_items)} invalid")
    return valid_items, invalid_items
