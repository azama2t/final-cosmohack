"""QA agent 19: relative links / backticked repo paths in README.md, docs/INDEX.md, docs/DEMO.md exist in git (git ls-files).
usage: python reports/qa/links_check.py <repo_root> <out.json>"""
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

repo, outp = Path(sys.argv[1]), Path(sys.argv[2])
files = set(subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, encoding="utf-8").stdout.splitlines())
dirs = {str(PurePosixPath(f).parents[i]) for f in files for i in range(len(PurePosixPath(f).parents) - 1)}
res = {}
for doc in ("README.md", "docs/INDEX.md", "docs/DEMO.md", "docs/CRITERIA_CHECK.md"):
    p = repo / doc
    if not p.is_file():
        continue
    t = p.read_text(encoding="utf-8")
    base = PurePosixPath(doc).parent
    bad, n = [], 0
    for m in re.finditer(r"\]\(([^)\s]+)\)", t):
        u = m.group(1).split("#")[0]
        if not u or re.match(r"^[a-z]+:", u):
            continue
        n += 1
        q = str(PurePosixPath(*(base / u).parts)) if not u.startswith("/") else u.lstrip("/")
        # normalise ../
        parts = []
        for x in PurePosixPath(q).parts:
            if x == "..":
                parts and parts.pop()
            elif x != ".":
                parts.append(x)
        q = "/".join(parts).rstrip("/")
        if q not in files and q not in dirs:
            bad.append(u)
    res[doc] = {"links": n, "missing": bad}
outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for d, r in res.items():
    print(d, "links", r["links"], "missing", len(r["missing"]), r["missing"][:15])
