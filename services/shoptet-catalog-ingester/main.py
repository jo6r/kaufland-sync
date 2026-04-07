"""Main entry point for Shoptet Catalog Ingester."""

import os
import logging
import argparse
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path

from shared_db.session import session_scope
from shared_db.dao import bulk_upsert_stock, set_last_run
from shared_db.trace_log import STAGE_SHOPTET_CATALOG, trace_line

from csv_parser import parse_csv, extract_fields
from validator import validate_items

logger = logging.getLogger(__name__)

JOB_NAME = "shoptet-catalog-ingester"


def get_ignore_codes() -> List[str]:
    """
    Get list of codes to ignore from environment variable.
    
    Reads IGNORE_CODES environment variable (comma-separated values).
    If not set, returns empty list.
    
    Returns:
        List of codes to ignore (normalized - stripped)
    """
    ignore_codes_str = os.getenv('IGNORE_CODES', '')
    if not ignore_codes_str:
        return []
    
    codes = [code.strip() for code in ignore_codes_str.split(',') if code.strip()]
    if codes:
        logger.info(f"Loaded {len(codes)} ignore codes from IGNORE_CODES environment variable")
    
    return codes


def read_csv_file(file_path: str) -> str:
    """
    Read CSV from local file.
    
    Args:
        file_path: Path to CSV file
        
    Returns:
        CSV content as string (decoded from Windows-1250)
        
    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file cannot be read
    """
    logger.info(f"Reading CSV from file: {file_path}")
    
    file = Path(file_path)
    if not file.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")
    
    # Read as binary first, then decode from Windows-1250
    with open(file, 'rb') as f:
        content_bytes = f.read()
    
    # Decode from Windows-1250
    content = content_bytes.decode('windows-1250')
    
    logger.info(f"Read {len(content_bytes)} bytes from file (decoded from Windows-1250)")
    return content


def download_csv(url: str, timeout: int = 30) -> str:
    """
    Download CSV from URL.
    
    Args:
        url: URL to download CSV from
        timeout: Request timeout in seconds
        
    Returns:
        CSV content as string (decoded from Windows-1250)
        
    Raises:
        requests.RequestException: If download fails
    """
    logger.info(f"Downloading CSV from {url}")
    
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    
    # Decode from Windows-1250
    content = response.content.decode('windows-1250')
    
    logger.info(f"Downloaded {len(response.content)} bytes (decoded from Windows-1250)")
    return content


def process_csv(csv_content: str) -> List[Dict[str, Any]]:
    """
    Process CSV content and extract valid items.
    
    Args:
        csv_content: CSV file content as string
        
    Returns:
        List of validated items ready for database insertion
    """
    # Load ignore codes once at the start
    ignore_codes = get_ignore_codes()
    
    # Parse CSV
    rows = parse_csv(csv_content)
    
    # Extract fields from each row
    items = []
    for row in rows:
        item = extract_fields(row, ignore_codes=ignore_codes)
        if item:
            items.append(item)
    
    # Validate items
    valid_items, invalid_items = validate_items(items)
    
    if invalid_items:
        logger.warning(f"Skipped {len(invalid_items)} invalid items")
    
    return valid_items


def ingest_stock(items: List[Dict[str, Any]]) -> None:
    """
    Ingest stock items into database.
    
    Args:
        items: List of validated items with keys: ean, code, name, qty
    """
    if not items:
        logger.warning("No items to ingest")
        return
    
    logger.info(f"Ingesting {len(items)} items into database")
    for item in items:
        ean = str(item.get("ean", ""))
        logger.info(
            trace_line(
                ean,
                STAGE_SHOPTET_CATALOG,
                f"catalog row qty={item.get('qty')} product_visibility={item.get('product_visibility')} code={item.get('code', '')}",
            )
        )

    with session_scope() as session:
        bulk_upsert_stock(session, items)
    
    logger.info(f"Successfully ingested {len(items)} items")


def update_job_checkpoint() -> None:
    """
    Update job checkpoint in job_state.
    """
    now = datetime.now(timezone.utc)
    
    logger.info(f"Updating job checkpoint: {JOB_NAME} at {now}")
    
    with session_scope() as session:
        set_last_run(session, JOB_NAME, now)
    
    logger.info(f"Job checkpoint updated successfully")


if __name__ == '__main__':
    # Setup argument parser
    parser = argparse.ArgumentParser(description='Shoptet Catalog Ingester')
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local file (products.csv from root directory) instead of URL'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Determine CSV source
    if args.local:
        csv_source = "products.csv"
        logger.info(f"Running single ingestion from file: {csv_source}")
    else:
        csv_source = os.getenv('SHOPTET_CSV_URL')
        if not csv_source:
            raise ValueError("SHOPTET_CSV_URL environment variable is not set")
        logger.info(f"Running single ingestion from URL: {csv_source}")
    
    # Run ingestion cycle
    try:
        logger.info(f"Starting ingestion cycle at {datetime.now(timezone.utc)}")
        
        # Load CSV
        if args.local:
            csv_content = read_csv_file(csv_source)
        else:
            csv_content = download_csv(csv_source)
        
        # Process CSV
        items = process_csv(csv_content)
        
        # Ingest to database
        if items:
            ingest_stock(items)
            logger.info(f"Ingestion cycle completed successfully: {len(items)} items processed")
        else:
            logger.warning("No valid items found in CSV")
        
        # Update job checkpoint
        update_job_checkpoint()
            
    except Exception as e:
        logger.error(f"Error during ingestion cycle: {e}", exc_info=True)
        # Update job checkpoint even on error (to track last attempt)
        try:
            update_job_checkpoint()
        except Exception as checkpoint_error:
            logger.error(f"Failed to update job checkpoint: {checkpoint_error}")
        raise
