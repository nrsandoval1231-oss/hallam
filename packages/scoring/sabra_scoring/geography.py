"""Country detection for remote postings (see `sabra_domain.geography`).

The tables and the detector live in the domain package because the search profile
validates its `countries` list with them; the scorer reaches them through here.
"""

from sabra_domain.geography import (
    ANYWHERE,
    describe,
    detect_regions,
    normalize_countries,
    remote_region_conflict,
    resolve_country,
)

__all__ = [
    "ANYWHERE",
    "describe",
    "detect_regions",
    "normalize_countries",
    "remote_region_conflict",
    "resolve_country",
]
