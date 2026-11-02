"""Metro areas expand to their principal cities, guarded by home state.

The regression this pins: a search profile that says "Dallas-Fort Worth" must
admit Plano, Irving, Arlington and Fort Worth without the candidate enumerating
suburbs — and must never admit "Dallas, GA" or "Bedford, MA" on the way.
"""

from __future__ import annotations

from sabra_domain.geography import (
    find_metro,
    known_metro_names,
    metro_matches_location,
)
from sabra_domain.schemas import LocationIntent
from test_m3_intelligence import assess, evaluate, make_job, make_search

DFW = "Dallas-Fort Worth"


def _metro_search(**overrides):
    return make_search(
        locations=LocationIntent(
            cities=[],
            states=["TX"],
            metro_areas=[DFW],
            allow_remote=True,
            allow_hybrid=True,
        ),
        **overrides,
    )


def test_dfw_admits_its_principal_cities() -> None:
    for location in (
        "Plano, TX",
        "Irving, TX",
        "Arlington, TX",
        "Fort Worth, TX",
        "Frisco, Texas",
        "The Colony, TX",
        "North Richland Hills, TX",
        "Dallas-Fort Worth, TX",
        "DFW",
    ):
        assert metro_matches_location(DFW, location), location


def test_dfw_never_admits_same_named_cities_in_other_states() -> None:
    for location in ("Dallas, GA", "Bedford, MA", "Garland, UT", "Denton, KS", "Denver, CO"):
        assert not metro_matches_location(DFW, location), location


def test_a_location_without_a_state_gets_the_benefit_of_the_doubt() -> None:
    # US boards often post a bare city; "Plano" alone should not be lost.
    assert metro_matches_location(DFW, "Plano")
    assert not metro_matches_location(DFW, "Houston")


def test_other_registered_metros_work() -> None:
    assert metro_matches_location("Houston", "Sugar Land, TX")
    assert metro_matches_location("Houston", "The Woodlands, TX")
    assert metro_matches_location("Austin", "Round Rock, TX")
    assert metro_matches_location("San Antonio", "Schertz, TX")
    assert metro_matches_location("New York", "Jersey City, NJ")  # multi-state metro
    assert metro_matches_location("Atlanta", "Alpharetta, GA")
    assert not metro_matches_location("Houston", "Plano, TX")


def test_aliases_resolve_to_the_same_metro() -> None:
    for alias in ("Dallas-Fort Worth", "Dallas/Fort Worth", "DFW", "Dallas", "Metroplex"):
        assert find_metro(alias) is find_metro(DFW), alias


def test_an_unrecognized_metro_falls_back_to_literal_phrase_matching() -> None:
    assert metro_matches_location("Research Triangle", "Research Triangle Park, NC")
    assert not metro_matches_location("Research Triangle", "Cary, NC")


def test_the_registry_is_discoverable() -> None:
    names = known_metro_names()
    assert DFW in names and "Houston" in names and "New York" in names


def test_hard_filter_admits_metro_suburbs_and_rejects_other_states() -> None:
    search = _metro_search()
    suburb = evaluate(make_job(location="Plano, TX"), search)
    assert suburb.passed, suburb.detail
    out_of_state = evaluate(make_job(location="Marietta, GA"), search)
    assert not out_of_state.passed
    assert out_of_state.reasons == ["PROHIBITED_GEOGRAPHY"]


def test_fit_scores_a_metro_suburb_as_preferred_metro() -> None:
    search = _metro_search()
    suburb = assess(make_job(location="Plano, TX"), search)
    assert suburb.features["components"]["location_score"]["details"]["tier"] == "preferred_metro"
    elsewhere_in_state = assess(make_job(location="Houston, TX"), search)
    assert elsewhere_in_state.features["components"]["location_score"]["details"]["tier"] == (
        "accepted_state"
    )
    assert suburb.location_score > elsewhere_in_state.location_score
