"""Answer keys the form adapters know how to fill.

The dashboard dropdown is this set, plus a free-form custom key the owner types.
Keys come from each adapter's `FIELD_KEYS` and from the question catalogue.
"""

from __future__ import annotations


def answer_key_choices() -> list[str]:
    from sabra_documents.answers import QUESTION_CATALOGUE

    from .greenhouse_adapter import GreenhouseApplicationAdapter
    from .lever_adapter import LeverApplicationAdapter

    keys = {key for key, _phrases in QUESTION_CATALOGUE}
    keys |= set(GreenhouseApplicationAdapter.FIELD_KEYS)
    keys |= set(LeverApplicationAdapter.FIELD_KEYS)
    return sorted(keys)
