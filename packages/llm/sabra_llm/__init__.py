"""Reasoning-layer abstraction (requirement 37).

Code owns truth; models assist judgment. The deterministic path must never
depend on a model being configured, so the default provider is `none` and every
caller is required to have a deterministic fallback. A model that is unavailable
degrades the answer; it never blocks the loop.
"""

__version__ = "0.1.0"
