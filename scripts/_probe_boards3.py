"""Round 3 board probe: more seed-vertical employers (aesthetics, skincare, beauty,
medical device and diagnostics commercial organisations). Not part of the product."""

from __future__ import annotations

import json
import sys

import _probe_boards as base

ROUND3 = [
    # aesthetics / devices / injectables (alternate spellings and subsidiaries)
    "evolusmedicalinc", "galderma", "galderma-us", "merz-north-america", "merzus",
    "solta", "soltamedicalinc", "cynosure", "cuteracorp", "inmode", "inmodeaesthetics",
    "scitoninc", "sciton", "lumenis", "candela-medical", "candelamedical",
    "bauschhealth-us", "bausch", "ortho-dermatologics", "skintuitive",
    "suneva", "sunevamedical", "aesthetics-biomedical", "cartessa", "cartessacorp",
    "rohreraesthetics", "crown-laboratories", "crownlabs", "prollenium",
    "sofwave", "sofwave-medical", "lutronic", "venusconcept", "venusconceptinc",
    "strataspharma", "strata", "encore-derm", "encorederm", "aestheticdevices",
    "aestheticssolutions", "idealimage", "sonobello", "sonobelloskinny",
    "laserskinclinics", "skinspirit", "elmc", "elmderm",
    # skincare / beauty tech / DTC with field teams
    "olaplex", "k18hair", "k18", "oliveandjune", "crownaffair", "briogeo",
    "patmcgrath", "patmcgrathlabs", "rawsugar", "auracolors", "summerfridays",
    "charlottetilbury", "rarebeauty", "meritbeauty", "ilia-beauty",
    "versedskin", "versed", "glowrecipe", "glowrecipeinc", "youthtothepeople",
    "bysoma", "drunkelephant", "drunkelephantinc", "paulaschoice",
    "theordinary", "deciem", "beautybay", "space-nk", "spacenk",
    "lush", "lushcosmetics", "freshbeauty", "farmacybeauty", "sephora", "ultabeauty",
    "creditedbeauty", "beautypie", "beautypiegroup",
    # medical device / diagnostics / health tech with field sales
    "sonosite", "fujifilm", "mindray", "mindray-north-america", "ambu", "baxterinc",
    "massdevice", "meritmedical", "angiodynamics", "teleflexmedical", "icumedical",
    "arthrex", "smithnephew", "conmed", "globusmedicalinc", "seaspine", "orthofix",
    "alphatecspine", "paragonsurgical", "tactile-medical", "tactilemedical",
    "heartflowinc", "elekta", "variansystems", "accuray", "medpace", "syneoshealth",
    "iqvia", "ppd", "amarin", "supernus", "harmonybiosciences", "catalystpharma",
    "collegiumpharma", "irontherapeutics", "malinckrodt",
]


def main() -> None:
    base.CANDIDATES = ROUND3
    rows: list[dict] = []
    with base.cf.ThreadPoolExecutor(max_workers=12) as pool:
        for result in pool.map(base.probe, base.CANDIDATES):
            rows.extend(result)
    live = [r for r in rows if r["status"] == 200 and r["count"] > 0]
    live.sort(key=lambda r: -r["count"])
    with open("_probe_results3.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"probed {len(ROUND3)} tokens x2 = {len(rows)} requests; live={len(live)}")
    for r in live:
        print(f"  {r['source']:11s} {r['token']:30s} jobs={r['count']}")


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
