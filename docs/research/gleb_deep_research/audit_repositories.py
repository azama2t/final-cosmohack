"""Collect current GitHub/Hugging Face metadata for shortlisted research code."""
import json
from pathlib import Path
import requests

REPOS = [
    "amonleong/MARLIT",
    "MarcCoru/marinedebrisdetector",
    "AIRCentre/POS2IDON",
    "marine-debris/marine-debris.github.io",
    "danieltyukov/marine-debris-ml-model",
    "Alexandre-Delplanque/HerdNet",
    "microsoft/MegaDetector-Overhead",
    "tamirshor7/RS-OVC",
    "gaoguangshuai/Counting-from-Sky-A-Large-scale-Dataset-for-Remote-Sensing-Object-Counting-and-A-Benchmark-Method",
    "weecology/DeepForest",
    "efcaguab/sar-vessel-detector",
    "aquab1t/sargassum-satellite-ml",
    "m7mdehab/oil-spill-detection",
]
OUT = Path(__file__).with_name("repo_audit.json")


def main():
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    rows = []
    for name in REPOS:
        response = session.get(f"https://api.github.com/repos/{name}", timeout=25)
        if response.status_code != 200:
            rows.append({"name": name, "status": response.status_code})
            continue
        item = response.json()
        rows.append({
            "name": name,
            "url": item["html_url"],
            "updated_at": item["updated_at"],
            "pushed_at": item["pushed_at"],
            "archived": item["archived"],
            "size_kb": item["size"],
            "license": (item.get("license") or {}).get("spdx_id"),
            "default_branch": item["default_branch"],
            "stars": item["stargazers_count"],
            "open_issues": item["open_issues_count"],
        })
        print(name, rows[-1].get("pushed_at"), rows[-1].get("license"), flush=True)
    OUT.write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
