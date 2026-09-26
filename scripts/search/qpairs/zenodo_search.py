"""Zenodo API: поиск записей (size ≤ 25 без токена; UA 'curl', браузерный UA получает 403); кэш data/extra/qpairs/zenodo/.
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/zenodo_search.py '"litter windrows"' 'windrow Sentinel-2' ..."""
import sys, urllib.parse
from _common import RAW, get_json, slug

if __name__ == "__main__":
    for q in sys.argv[1:]:
        d = get_json("https://zenodo.org/api/records?" + urllib.parse.urlencode({"q": q, "size": 25, "sort": "bestmatch"}),
                     RAW / "zenodo" / (slug(q, 80) + ".json"), sleep=1)
        print(f"=== {q}: total {d['hits']['total']}")
        for h in d["hits"]["hits"]:
            m = h["metadata"]
            print(" ", h["id"], m.get("publication_date"), (m.get("resource_type") or {}).get("type"), "|", m["title"][:110])
