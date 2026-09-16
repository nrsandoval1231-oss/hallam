"""Round 2 board probe: broader token sweep across aesthetics/device/beauty.

Not part of the product.
"""
from __future__ import annotations

import json
import sys

import _probe_boards as base

ROUND2 = [
    # aesthetics device / injectables / skincare science
    "evolusmedical", "evolusinc", "galdermaholdings", "galderma-us", "merzaestheticsus",
    "allerganaesthetics", "abbvieaesthetics", "sientrainc", "cuterainc", "cutera-inc",
    "inmodeltd", "inmodeaesthetics", "cynosureinc", "revanceaesthetics", "soltamedical",
    "venusconceptinc", "cartessaaesthetics", "sciton", "scitoninc", "lumenisinc",
    "rohreraesthetics", "crownlaboratories", "aestheticmanagementpartners",
    "beautyhealthcompany", "hydrafacial", "sofwave-medical", "lutronicinc",
    "prolleniummedical", "obagiinc", "alastin", "skinbetterscience", "skinbetter",
    "orthodermatologics", "biofronteraag", "eltamd", "isclinical", "zoskinhealth",
    "sinclairpharma", "hugelinc", "endoglobal", "endointernational",
    "biorasi", "candelamedicalinc", "strataspharma", "encoremedical",
    # DTC beauty / skincare / wellness
    "beautycounter", "k18hair", "amika", "briogeo", "ceramedx", "lolavie",
    "crownaffair", "necessaire", "versedskin", "versedbeauty", "musely",
    "thedermstore", "dermstore", "skinlaundry", "idealimagedirect", "laseraway",
    "milkshakehair", "oliveandjune", "dazzle-dry", "drybar", "bumbleandbumble",
    "ospreybeauty", "wellpeople", "juicebeauty", "kosas", "meritbeauty",
    "saltandstone", "truebotanicals", "beautyblender", "morphe", "kyliecosmetics",
    "colourpop", "jaclyncosmetics", "doctorrogers", "eightysixd", "ritual",
    "seedhealth", "athleticgreens", "ag1", "beam", "onedoctor", "mosh",
    "dukeandduchess", "hersphere",
    # med device / diagnostics commercial
    "shockwavemedical", "exactsciences", "natera", "guardanthealth", "tenexgenomics",
    "neogenomics", "myriadgenetics", "invitae", "tempus", "flatironhealth",
    "irenabstract", "verily", "butterflynetwork", "heartflow", "ceribell",
    "somalogic", "quanterix", "luminex", "orasure", "quidelortho",
    # healthcare services / distribution relevant to commercial roles
    "optioncare", "brightspring", "amedisys", "lhcgroup", "bayada",
    "surgerypartners", "usoncology", "villageMD", "oakstreethealth",
]


def main() -> None:
    base.CANDIDATES = ROUND2
    rows: list[dict] = []
    with base.cf.ThreadPoolExecutor(max_workers=12) as pool:
        for result in pool.map(base.probe, base.CANDIDATES):
            rows.extend(result)
    live = [r for r in rows if r["status"] == 200 and r["count"] > 0]
    live.sort(key=lambda r: -r["count"])
    with open("_probe_results2.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"probed {len(ROUND2)} tokens x2 = {len(rows)} requests; live={len(live)}")
    for r in live:
        print(f"  {r['source']:11s} {r['token']:28s} jobs={r['count']:<5d}")


if __name__ == "__main__":
    sys.path.insert(0, "scripts")
    main()
