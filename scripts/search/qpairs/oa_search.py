"""OpenAlex: поиск работ (с 2016 г.) по запросам; кэш data/extra/qpairs/openalex/.
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/oa_search.py "litter windrows Sentinel-2 in situ" ..."""
import sys, urllib.parse
from _common import RAW, get_json, slug

OUT = RAW / "openalex"
if __name__ == "__main__":
    for q in sys.argv[1:]:
        d = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"search": q, "per-page": 15, "filter": "from_publication_date:2016-01-01"}), OUT / (slug(q) + ".json"))
        print(f"=== {q}: {d['meta']['count']}")
        for w in d["results"]:
            print(" ", w["publication_year"], w.get("doi"), "|", (w["title"] or "")[:120])
