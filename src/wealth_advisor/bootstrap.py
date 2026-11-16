import sqlite3
from datetime import date
from pathlib import Path

from .data.demo_seed import seed_demo
from .data.real_fetcher import RealSyncReport, get_market_data_provider
from .data.stats import compute_all_stats
from .data.store import MetadataStore
from .rag.demo_corpus import build_demo_corpus
from .rag.index import VectorIndex


def bootstrap_demo(
    conn: sqlite3.Connection,
    as_of: date | None = None,
    include_extended: bool = False,
) -> int:
    """Load the synthetic fund universe, precompute stats and index the demo fact sheets."""
    store = MetadataStore(conn)
    n = seed_demo(store, as_of, include_extended=include_extended)
    compute_all_stats(store)
    build_demo_corpus(store, VectorIndex(conn))
    return n


def bootstrap_real(
    conn: sqlite3.Connection,
    *,
    csv_path: Path | None = None,
    provider_name: str | None = None,
    refresh_amfi: bool = False,
) -> RealSyncReport:
    """Load curated real mutual funds, fetch historical NAVs, compute stats, and index fact sheets."""
    store = MetadataStore(conn)
    provider = get_market_data_provider(provider_name)
    report = provider.sync(store, csv_path=csv_path, refresh_amfi=refresh_amfi)
    compute_all_stats(store)
    build_demo_corpus(store, VectorIndex(conn))
    return report

