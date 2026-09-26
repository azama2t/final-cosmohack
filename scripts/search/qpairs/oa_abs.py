"""OpenAlex: карточка работы по DOI (название, дата, OA-ссылка, аннотация); кэш data/extra/qpairs/openalex/w_*.json.
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/oa_abs.py 10.3389/fmars.2021.613399 ..."""
import sys
from _common import RAW, get_json, oa_abstract, slug

if __name__ == "__main__":
    for doi in sys.argv[1:]:
        w = get_json("https://api.openalex.org/works/doi:" + doi, RAW / "openalex" / ("w_" + slug(doi, 200) + ".json"))
        print("=====", doi, "|", w["title"], "|", w["publication_date"])
        print("  OA:", (w.get("open_access") or {}).get("oa_url"))
        print(" ", oa_abstract(w)[:1800])
