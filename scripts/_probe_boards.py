"""One-off probe: which ATS board tokens are live for seed-vertical companies.

Not part of the product. Used during discovery provider bring-up to establish
which real boards exist before wiring them into the source registry.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import urllib.error
import urllib.request

GH = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false"
LEVER = "https://api.lever.co/v0/postings/{token}?mode=json"

CANDIDATES = [
    # medical aesthetics / aesthetics devices
    "allergan", "abbvie", "evolus", "galderma", "merzaesthetics", "merz",
    "bauschhealth", "sientra", "cutera", "candelamedical", "candela", "inmode",
    "cynosure", "revance", "solta", "venusconcept", "cartessa", "sciton",
    "lumenis", "rohrer", "crownlaboratories", "crown", "aestheticmanagementpartners",
    "hydrafacial", "beautyhealth", "thebeautyhealthcompany", "sofwave", "lutronic",
    "prollenium", "obagi", "alastin", "skinceuticals", "loreal", "esteelauder",
    "skinbetter", "skinbetterscience", "ortho-dermatologics", "orthodermatologics",
    "endomagnetics", "endo", "biofrontera", "eltamd", "isclinical", "zoskin",
    "aestheticsbiomedical", "bioha", "sinclairpharma", "hugel",
    # beauty / skincare / wellness DTC
    "elfcosmetics", "elfbeauty", "olaplex", "honest", "honestcompany", "nutrafol",
    "hims", "forhims", "himsandhers", "curology", "grovecollaborative", "grove",
    "soldejaneiro", "drbarbarasturm", "biossance", "youthtothepeople",
    "glossier", "summerfridays", "supergoop", "ilia", "tatcha", "versed",
    "paulaschoice", "dermalogica", "marykay", "avon", "nu skin", "rodanandfields",
    "arvida", "mastglobal",
    # med device / life science commercial
    "medtronic", "bostonscientific", "stryker", "zimmerbiomet", "abbott",
    "edwards", "baxter", "bd", "teleflex", "integralife", "globusmedical",
    "penumbra", "masimo", "insulet", "dexcom", "tandemdiabetes", "nuvasive",
    "resmed", "hologic", "gehealthcare", "philips", "siemenshealthineers",
    "thermofisher", "danaher", "waters", "illumina", "biotechne", "agilent",
    "roche", "novartis", "pfizer", "johnsonandjohnson", "lilly", "amgen",
    # distribution / commercial partners in aesthetics
    "henryschein", "henryscheininc", "mckesson", "cardinalhealth", "patersoncos",
    "concordancehealthcare", "medline",
]


def head(url: str, timeout: float = 12.0) -> tuple[int, int, int]:
    """Return (status, count, bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": "sabra-probe/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        status = resp.status
    try:
        data = json.loads(body)
    except Exception:
        return status, -1, len(body)
    if isinstance(data, dict):
        jobs = data.get("jobs")
        count = len(jobs) if isinstance(jobs, list) else data.get("meta", {}).get("total", -1)
    elif isinstance(data, list):
        count = len(data)
    else:
        count = -1
    return status, count, len(body)


def probe(token: str) -> list[dict]:
    out = []
    for name, tmpl in (("greenhouse", GH), ("lever", LEVER)):
        url = tmpl.format(token=token)
        try:
            status, count, nbytes = head(url)
            out.append({"token": token, "source": name, "url": url,
                        "status": status, "count": count, "bytes": nbytes})
        except urllib.error.HTTPError as exc:
            out.append({"token": token, "source": name, "url": url,
                        "status": exc.code, "count": -1, "bytes": 0})
        except Exception as exc:  # network/timeout
            out.append({"token": token, "source": name, "url": url,
                        "status": 0, "count": -1, "bytes": 0,
                        "error": f"{type(exc).__name__}: {exc}"})
    return out


def main() -> None:
    rows: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        for result in pool.map(probe, CANDIDATES):
            rows.extend(result)
    live = [r for r in rows if r["status"] == 200 and r["count"] > 0]
    live.sort(key=lambda r: -r["count"])
    with open("_probe_results.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"probed {len(CANDIDATES)} tokens x2 sources = {len(rows)} requests")
    print(f"live boards with postings: {len(live)}")
    for r in live:
        print(f"  {r['source']:11s} {r['token']:28s} jobs={r['count']:<5d} {r['url']}")


if __name__ == "__main__":
    main()
