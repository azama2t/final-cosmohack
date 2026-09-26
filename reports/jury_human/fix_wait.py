import io, re, os
D = os.path.dirname(os.path.abspath(__file__))
for f in ["tasks33.py", "loadtime.py", "probe_list.py", "probe_studio.py"]:
    p = os.path.join(D, f); s = io.open(p, encoding="utf-8").read()
    s2, n = re.subn(r"/Спутниковые зоны детектора[^/]*/\.test\(document\.body\.innerText\)", "/30SXE · зона/.test(document.body.innerText)", s)
    io.open(p, "w", encoding="utf-8").write(s2); print(f, n)
