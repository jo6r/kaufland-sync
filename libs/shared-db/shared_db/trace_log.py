"""Stable tokens for correlating one EAN across services in aggregated logs.

In Docker/K8s/Loki: search ``trace_ean=<EAN>`` to see how that EAN passed through jobs.
Stage names identify which pipeline step produced the line.
"""

# Log field name — keep stable for grep / log queries
TRACE_FIELD = "trace_ean"

# Shoptet → Kaufland pipeline
STAGE_SHOPTET_CATALOG = "shoptet_catalog_ingest"
STAGE_SHOPTET_UNIT_MAP = "shoptet_unit_map"
STAGE_SHOPTET_STOCK = "shoptet_stock_update"

# Jiri feed pipeline
STAGE_JIRI_FEED = "jiri_feed"
STAGE_JIRI_STOCK = "jiri_stock_update"


def trace_line(ean: str, stage: str, detail: str) -> str:
    """Build message: trace_ean=<ean> stage=<stage> <detail>"""
    return f"{TRACE_FIELD}={ean} stage={stage} {detail}"
