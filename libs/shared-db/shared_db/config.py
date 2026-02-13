"""Database configuration."""

import os


def get_db_dsn() -> str:
    """
    Get database DSN from environment variables.
    
    Can use either:
    - DB_DSN: Full connection string (takes precedence)
    - DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME: Individual components
    
    Returns:
        Database connection string.
        
    Raises:
        ValueError: If required environment variables are not set.
    """
    # Check if full DSN is provided (takes precedence)
    dsn = os.getenv('DB_DSN')
    if dsn:
        return dsn
    
    # Otherwise, build DSN from individual components
    host = os.getenv('DB_HOST')
    port = os.getenv('DB_PORT', '3306')
    user = os.getenv('DB_USER')
    password = os.getenv('DB_PASSWORD')
    database = os.getenv('DB_NAME')
    
    if not host:
        raise ValueError('Either DB_DSN or DB_HOST environment variable must be set')
    if not user:
        raise ValueError('DB_USER environment variable must be set')
    if not password:
        raise ValueError('DB_PASSWORD environment variable must be set')
    if not database:
        raise ValueError('DB_NAME environment variable must be set')
    
    # Build MariaDB/MySQL connection string
    return f"mariadb+pymysql://{user}:{password}@{host}:{port}/{database}"
