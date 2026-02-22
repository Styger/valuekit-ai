"""
Smart Cache Manager für ValueKit
Handles file-based caching mit Pickle für verschiedene Datentypen
"""

import pickle
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional, Callable, Dict
import sys
import hashlib
import logging

from backend.valuekit_ai.config.config import PIPELINE_VERSION

log = logging.getLogger(__name__)

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


class CacheManager:
    """
    Intelligentes Caching System für API Daten

    Features:
    - File-based persistence (Pickle)
    - TTL (Time-To-Live) pro Datentyp
    - Automatisches Expiry-Check
    - Metadata-Tracking
    """

    # Cache Rules: TTL in Sekunden (None = never expires)
    # Cache Rules: TTL in Sekunden (None = never expires)
    CACHE_RULES = {
        "sec_10k": None,  # Forever (immutable)
        "earnings": None,  # Forever (immutable)
        "historical_prices": None,  # Forever (immutable)
        "historical_fundamentals": None,  # ← NEW: Forever (historical data immutable!)
        "fundamentals": 7 * 86400,  # 7 days
        "current_price": 86400,  # 1 day
        "news": 7 * 86400,  # 7 days
        "short_interest": 30 * 86400,  # 30 days
    }

    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Initialize Cache Manager

        Args:
            cache_dir: Custom cache directory (default: backend/valuekit_ai/data/cache/)
        """
        if cache_dir is None:
            # Default cache location
            self.cache_dir = (
                Path(__file__).resolve().parent.parent.parent / "data" / "cache"
            )
        else:
            self.cache_dir = Path(cache_dir)

        # Create cache directories
        self._init_directories()

        # Load or create metadata
        self.metadata_file = self.cache_dir / "metadata.json"
        self.metadata = self._load_metadata()

    def _init_directories(self):
        """Create cache directory structure"""
        directories = [
            self.cache_dir,
            self.cache_dir / "sec_filings",
            self.cache_dir / "earnings",
            self.cache_dir / "prices",
            self.cache_dir / "fundamentals",
            self.cache_dir / "news",
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def _load_metadata(self) -> Dict:
        """Load cache metadata from JSON"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("[cache_manager][metadata_load_error] error=%s", e)
                return {}
        return {}

    def _save_metadata(self):
        """Save cache metadata to JSON"""
        try:
            with open(self.metadata_file, "w") as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            log.warning("[cache_manager][metadata_save_error] error=%s", e)

    def _get_cache_path(self, key: str, data_type: str) -> Path:
        """Get cache file path for a key"""
        # Map data types to subdirectories
        subdir_map = {
            "sec_10k": "sec_filings",
            "earnings": "earnings",
            "historical_prices": "prices",
            "current_price": "prices",
            "fundamentals": "fundamentals",
            "historical_fundamentals": "fundamentals",  # ← NEW
            "news": "news",
            "short_interest": "fundamentals",
        }

        subdir = subdir_map.get(data_type, "misc")
        cache_subdir = self.cache_dir / subdir
        cache_subdir.mkdir(exist_ok=True)

        safe_key = key.replace("/", "_").replace("\\", "_")
        return cache_subdir / f"{safe_key}.pkl"

    def _is_expired(self, key: str, data_type: str) -> bool:
        """
        Check if cached data is expired

        Args:
            key: Cache key
            data_type: Type of data

        Returns:
            True if expired, False otherwise
        """
        # Get TTL for this data type
        ttl = self.CACHE_RULES.get(data_type)

        # None = never expires
        if ttl is None:
            return False

        # Check metadata for timestamp
        if key not in self.metadata:
            return True  # No metadata = expired

        cached_time = datetime.fromisoformat(self.metadata[key]["timestamp"])
        now = datetime.now()

        return (now - cached_time).total_seconds() > ttl

    def get(self, key: str, data_type: str) -> Optional[Any]:
        cache_path = self._get_cache_path(key, data_type)
        if not cache_path.exists():
            return None
        if self._is_expired(key, data_type):
            log.info("[cache_manager][expired] key=%s", key)
            return None
        try:
            raw = cache_path.read_bytes()
            expected_sha = self.metadata.get(key, {}).get("sha256")
            if expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha:
                log.warning("[cache_manager][integrity_fail] key=%s — deleting", key)
                cache_path.unlink()
                return None
            log.info("[cache_manager][hit] key=%s", key)
            return pickle.loads(raw)
        except Exception as e:
            log.error("[cache_manager][get_error] key=%s error=%s", key, e)
            return None

    def set(self, key: str, data_type: str, data: Any):
        cache_path = self._get_cache_path(key, data_type)
        try:
            raw = pickle.dumps(data)
            sha = hashlib.sha256(raw).hexdigest()
            cache_path.write_bytes(raw)
            self.metadata[key] = {
                "timestamp": datetime.now().isoformat(),
                "data_type": data_type,
                "file": str(cache_path),
                "sha256": sha,
                "pipeline_version": PIPELINE_VERSION,
            }
            self._save_metadata()
            log.info("[cache_manager][set] key=%s type=%s", key, data_type)
        except Exception as e:
            log.error("[cache_manager][set_error] key=%s error=%s", key, e)

    def get_or_fetch(
        self, key: str, data_type: str, fetch_fn: Callable[[], Any]
    ) -> Any:
        """
        Get from cache or fetch fresh data

        This is the main API - combines get() and set()

        Args:
            key: Cache key (e.g., "AAPL_10K_2024")
            data_type: Type of data (determines TTL)
            fetch_fn: Function to call if cache miss (e.g., lambda: api.fetch_10k("AAPL"))

        Returns:
            Data (from cache or freshly fetched)

        Example:
            cache = CacheManager()
            data = cache.get_or_fetch(
                key="AAPL_10K_2024",
                data_type="sec_10k",
                fetch_fn=lambda: fetch_sec_10k("AAPL", 2024)
            )
        """
        # Try cache first
        cached = self.get(key, data_type)
        if cached is not None:
            return cached

        # Cache miss - fetch fresh
        log.info("[cache_manager][miss] key=%s — fetching", key)
        fresh_data = fetch_fn()

        # Save to cache
        self.set(key, data_type, fresh_data)

        return fresh_data

    def clear(self, key: Optional[str] = None, data_type: Optional[str] = None):
        """
        Clear cache entries

        Args:
            key: Specific key to clear (None = clear all)
            data_type: Clear all entries of this type (None = all types)
        """
        if key:
            # Clear specific key
            cache_path = self._get_cache_path(key, data_type or "misc")
            if cache_path.exists():
                cache_path.unlink()
                log.info("[cache_manager][clear] key=%s", key)

            if key in self.metadata:
                del self.metadata[key]
                self._save_metadata()
        else:
            # Clear all or by type
            cleared = 0
            for cache_key in list(self.metadata.keys()):
                if (
                    data_type is None
                    or self.metadata[cache_key]["data_type"] == data_type
                ):
                    cache_file = Path(self.metadata[cache_key]["file"])
                    if cache_file.exists():
                        cache_file.unlink()
                        cleared += 1
                    del self.metadata[cache_key]

            self._save_metadata()
            log.info("[cache_manager][clear] cleared=%d entries", cleared)

    def get_stats(self) -> Dict:
        """Get cache statistics"""
        import os  # ← ADD THIS

        total_size = 0
        file_count = 0

        # FIX: Use os.walk() instead of Path.walk()
        for root, dirs, files in os.walk(self.cache_dir):  # ← CHANGE THIS
            for file in files:
                if file.endswith(".pkl"):
                    file_path = Path(root) / file  # ← Convert to Path
                    total_size += file_path.stat().st_size
                    file_count += 1

        size_mb = total_size / (1024 * 1024)

        return {
            "total_size_mb": round(size_mb, 2),
            "file_count": file_count,
            "metadata_entries": len(self.metadata),
        }


# Singleton instance for easy import
_cache_instance = None


def get_cache_manager() -> CacheManager:
    """Get singleton CacheManager instance"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheManager()
    return _cache_instance
