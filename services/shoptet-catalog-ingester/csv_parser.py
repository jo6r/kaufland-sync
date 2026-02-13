"""CSV parser for Shoptet catalog."""

import csv
import io
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def parse_csv(csv_content: str, encoding: str = 'windows-1250') -> List[Dict[str, Any]]:
    """
    Parse CSV content into list of dictionaries.
    
    Robust against empty columns and handles various CSV formats.
    
    Args:
        csv_content: CSV file content as string (or bytes)
        encoding: Encoding of the CSV content (default: windows-1250)
        
    Returns:
        List of dictionaries with parsed rows
        
    Raises:
        ValueError: If CSV is malformed or cannot be parsed
    """

    # Use StringIO to read from string
    csv_file = io.StringIO(csv_content)
    
    reader = csv.DictReader(csv_file, delimiter=";")
    
    rows = []
    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
        # Clean up the row - remove None values and empty strings from keys
        cleaned_row = {}
        for key, value in row.items():
            # Strip whitespace from key
            clean_key = key.strip() if key else None
            if clean_key:
                # Handle empty values - convert to None or empty string
                clean_value = value.strip() if value else ''
                cleaned_row[clean_key] = clean_value
        
        if cleaned_row:  # Only add non-empty rows
            rows.append(cleaned_row)
    
    logger.info(f"Parsed {len(rows)} rows from CSV")
    return rows


def extract_fields(row: Dict[str, Any], ignore_codes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Extract fields from CSV row.
    
    Args:
        row: Dictionary with CSV row data
        ignore_codes: Optional list of codes to ignore (if None, will load from environment)
        
    Returns:
        Dictionary with keys: ean, code, name, qty, product_visibility or None if extraction fails
    """
    # Case-insensitive field matching
    row_lower = {k.lower().strip(): v for k, v in row.items()}
    
    # Extract fields directly
    ean = row_lower.get('ean', '').strip()
    code = row_lower.get('code', '').strip()
    name = row_lower.get('name', '').strip()
    stock = row_lower.get('stock', '').strip()
    product_visibility = row_lower.get('productvisibility', '').strip()
    
    # Skip items with productVisibility == "hidden"
    if product_visibility.lower() == 'hidden':
        return None
    
    # Skip items with code in ignore list
    if ignore_codes and code and code.lower() in [ic.lower() for ic in ignore_codes]:
        logger.info(f"Skipping code: {code} (EAN: {ean}) - code is in ignore list")
        return None
    
    if not ean or not stock:
        return None
    
    # Convert stock to int
    try:
        stock_int = int(float(stock))
    except (ValueError, TypeError):
        return None
    
    # Handle negative stock values - change to 0 and log
    if stock_int < 0:
        logger.error(f"Negative stock value detected for code {code} (EAN: {ean}): {stock_int}. Changing to 0.")
        stock_int = 0
        logger.info(f"Stock value changed to 0 for code {code} (EAN: {ean})")
    
    return {
        'ean': ean,
        'code': code or '',
        'name': name or '',
        'qty': stock_int,
        'product_visibility': product_visibility
    }
