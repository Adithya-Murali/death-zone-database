"""Code tables for the Himalayan Database's integer-coded columns.

The upstream tables store categoricals as small integers whose meanings are not
in the data. Every mapping below is transcribed from **Appendix B: Database
Structure** of the Himalayan Database Program Guide (June 2026 edition), which
prints the code lists inline with each field. The official program exposes the
same decodings as SQL functions -- Season(), Host(), Reason(), Death(),
DeathClass(), Injury() -- so these are reimplementations, not inventions.

A previous version of this file inferred SEASON and TERMREASON from the data's
own behaviour and carried a provenance warning. That guesswork is gone: the
labels here match the Guide's wording, and the one code it had invented
(TERMREASON 15) does not exist upstream or in the data.
"""

from __future__ import annotations

from collections.abc import Iterator

SEASON = {0: "Unknown", 1: "Spring", 2: "Summer", 3: "Autumn", 4: "Winter"}

HOST = {0: "Unknown", 1: "Nepal", 2: "China", 3: "India"}

TERM_REASON = {
    0: "Unknown",
    1: "Success (main peak)",
    2: "Success (subpeak, foresummit)",
    3: "Success (claimed)",
    4: "Bad weather (storms, high winds)",
    5: "Bad conditions (deep snow, avalanching, falling ice, or rock)",
    6: "Accident (death or serious injury)",
    7: "Illness, AMS, exhaustion, or frostbite",
    8: "Lack (or loss) of supplies, support or equipment",
    9: "Lack of time",
    10: "Route technically too difficult, lack of experience, strength, or motivation",
    11: "Did not reach base camp",
    12: "Did not attempt climb",
    13: "Attempt rumored",
    14: "Other",
}

# TERMREASON is NOT a failure column: codes 1-3 are successes and account for
# 6,843 of 11,854 expeditions. Filter with `NOT IN` on this set, never on NULL.
SUCCESS_TERM_REASONS = frozenset({1, 2, 3})

HIMAL = {
    0: "Unclassified",
    1: "Annapurna",
    2: "Api/Byas Risi/Guras",
    3: "Damodar",
    4: "Dhaulagiri",
    5: "Ganesh/Shringi",
    6: "Janak/Ohmi Kangri",
    7: "Jongsang",
    8: "Jugal",
    9: "Kangchenjunga/Simhalila",
    10: "Kanjiroba",
    11: "Kanti/Palchung",
    12: "Khumbu",
    13: "Langtang",
    14: "Makalu",
    15: "Manaslu/Mansiri",
    16: "Mukut/Mustang",
    17: "Nalakankar/Chandi/Changla",
    18: "Peri",
    19: "Rolwaling",
    20: "Saipal",
}

REGION = {
    0: "Unclassified",
    1: "Kangchenjunga-Janak",
    2: "Khumbu-Rolwaling-Makalu",
    3: "Langtang-Jugal",
    4: "Manaslu-Ganesh",
    5: "Annapurna-Damodar-Peri",
    6: "Dhaulagiri-Mukut",
    7: "Kanjiroba-Far West",
}

PEAK_HOST = {
    0: "Unclassified",
    1: "Nepal only",
    2: "China only",
    3: "India only",
    4: "Nepal & China",
    5: "Nepal & India",
    6: "Nepal, China & India",
}

PEAK_STATUS = {0: "Unknown", 1: "Unclimbed", 2: "Climbed"}

SUMMIT_BID = {
    0: "Unspecified",
    1: "No summit bid",
    2: "Aborted below high camp",
    3: "Aborted at high camp",
    4: "Aborted above high camp",
    5: "Successful summit bid",
}

# Distinct from TERM_REASON: same idea, different scheme, different numbering.
# MSMTTERM 3 is "bad weather"; TERMREASON 3 is "success (claimed)".
SUMMIT_BID_TERM = {
    0: "Unspecified",
    1: "Success",
    2: "Success (subpeak, foresummit)",
    3: "Bad weather (storms, high winds)",
    4: "Bad conditions (deep snow, avalanches, falling rock/ice)",
    5: "Accident (death or injury to self or others)",
    6: "Altitude (AMS symptoms, breathing or unwell)",
    7: "Exhaustion, fatigue, weakness or lack of motivation",
    8: "Frostbite, snowblindness or coldness",
    9: "Other illnesses or pains",
    10: "Lack of supplies, support or equipment problems",
    11: "O2 system failure",
    12: "Route difficulty, intimidation or insufficient ability",
    13: "Too late in day or too slow",
    14: "Assisting, guiding, supporting or accompanying others",
    15: "Route/camp preparation or rope fixing",
    16: "Insufficient time left for expedition",
    17: "Did not climb or intent to summit",
    18: "Other",
    19: "Unknown",
    20: "Did not attempt additional peak after 1st success",
}

# DEATHTYPE and INJURYTYPE share one scheme in Appendix B.
HARM_TYPE = {
    0: "Unspecified",
    1: "AMS (acute mtn sickness)",
    2: "Exhaustion",
    3: "Exposure / frostbite",
    4: "Fall",
    5: "Crevasse",
    6: "Icefall collapse",
    7: "Avalanche",
    8: "Falling rock / ice",
    9: "Disappearance (unexplained)",
    10: "Illness (non-AMS)",
    11: "Other",
    12: "Unknown",
}

DEATH_CLASS = {
    0: "Unspecified",
    1: "Death enroute BC",
    2: "Death at BC / ABC",
    3: "Route preparation",
    4: "Ascending in summit bid",
    5: "Descending from summit bid",
    6: "Expedition evacuation",
    7: "Other / Unknown",
}

# Domain names used by the `label(domain, code)` macro in silver.
DOMAINS: dict[str, dict[int, str]] = {
    "season": SEASON,
    "host": HOST,
    "term_reason": TERM_REASON,
    "himal": HIMAL,
    "region": REGION,
    "peak_host": PEAK_HOST,
    "peak_status": PEAK_STATUS,
    "summit_bid": SUMMIT_BID,
    "summit_bid_term": SUMMIT_BID_TERM,
    "harm_type": HARM_TYPE,
    "death_class": DEATH_CLASS,
}


def rows() -> Iterator[tuple[str, int, str]]:
    """Every mapping in long form, for loading into one lookup table."""
    for domain, mapping in DOMAINS.items():
        for code, label in mapping.items():
            yield domain, code, label
