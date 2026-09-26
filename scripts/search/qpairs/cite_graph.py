"""L105 / §18: граф цитирований через OpenAlex. Собирает работы, которые цитируют заданные статьи, и отбирает кандидатов
с полевым счётом плавающего мусора и (или) снимками. Кэш — data/extra/qpairs/openalex/cites_*.json.
Выход: data/extra/qpairs/cite_graph.csv (все цитирующие) и cite_graph_candidates.csv (отбор по ключевым словам).
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/cite_graph.py"""
import csv
import re
import urllib.parse

from _common import RAW, get_json, oa_abstract, slug

SEEDS = {  # DOI опорных работ (§18 + сообщение оркестратора)
    "Hajbane2021_Ashmore": "10.3389/fmars.2021.613399",
    "GarciaGarin2020_drone_vessel": "10.1016/j.marpolbul.2020.111467",
    "Cozar2024_LWD": "10.1038/s41467-024-48674-7",
    "Cozar2021_windrows": "10.3389/fmars.2021.571796",
    "Lambert2020_ASI_debris": "10.1016/j.envpol.2020.114430",
    "Gove2019_slicks": "10.1073/pnas.1907496116",
}
# кандидат = есть признак полевого счёта И признак изображения/площади (или набор данных)
FIELD = re.compile(r"\b(drone|uav|unmanned|aerial survey|aircraft|vessel|ship-based|visual (survey|observ|count)|transect|"
                   r"manta|neuston|items?\s*(km|per)|counted|abundance|density of (floating|litter|debris))", re.I)
IMAGE = re.compile(r"\b(sentinel|landsat|planet|satellite|orthomosaic|uav|drone|aerial|imagery|windrow|front|slick|"
                   r"convergence|dataset|data set)", re.I)
TOPIC = re.compile(r"(litter|debris|plastic|macroplastic|flotsam|garbage|trash)", re.I)


def citing(doi: str):
    w = get_json("https://api.openalex.org/works/doi:" + doi, RAW / "openalex" / ("w_" + slug(doi, 200) + ".json"))
    wid = w["id"].rsplit("/", 1)[-1]
    out, cursor = [], "*"
    while cursor:
        d = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"filter": f"cites:{wid}", "per-page": 200, "cursor": cursor}),
            RAW / "openalex" / f"cites_{wid}_{slug(cursor, 40)}.json")
        out += d["results"]
        cursor = d["meta"].get("next_cursor") if d["results"] else None
    return w, out


def main():
    rows = []
    for key, doi in SEEDS.items():
        try:
            w, cs = citing(doi)
        except Exception as e:  # noqa: BLE001
            print(key, "ERROR", e)
            continue
        print(f"{key}: {w['title'][:70]} — цитирований {len(cs)}")
        for c in cs:
            ab = oa_abstract(c)
            txt = (c.get("title") or "") + " " + ab
            rows.append(dict(seed=key, doi=c.get("doi"), year=c.get("publication_year"), type=c.get("type"),
                             title=(c.get("title") or "")[:200], oa_url=(c.get("open_access") or {}).get("oa_url"),
                             topic=bool(TOPIC.search(txt)), field=bool(FIELD.search(txt)), image=bool(IMAGE.search(txt)),
                             abstract=ab[:1500]))
    keys = list(rows[0])
    with open(RAW / "cite_graph.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, keys)
        wr.writeheader()
        wr.writerows(rows)
    seen, cand = set(), []
    for r in rows:
        if r["topic"] and r["field"] and r["image"] and r["year"] and r["year"] >= 2016 and r["doi"] not in seen:
            seen.add(r["doi"])
            cand.append(r)
    with open(RAW / "cite_graph_candidates.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, keys)
        wr.writeheader()
        wr.writerows(cand)
    print("всего строк", len(rows), "уникальных DOI", len({r["doi"] for r in rows}), "кандидатов", len(cand))


if __name__ == "__main__":
    main()
