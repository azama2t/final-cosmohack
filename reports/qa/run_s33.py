"""QA agent 19: run scripts/case/s33_path.py against a service, with frames/JSON redirected to reports/qa/img/s33_<stamp>/
(so reports/case_demo/ is not touched).  usage: python reports/qa/run_s33.py <base> <stamp> [repo_root]"""
import importlib.util
import sys
from pathlib import Path

base, stamp = sys.argv[1], sys.argv[2]
repo = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("s33_path", repo / "scripts" / "case" / "s33_path.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
out = Path(__file__).resolve().parent / "img" / f"s33_{stamp}"
out.mkdir(parents=True, exist_ok=True)
m.OUT = out
sys.argv = ["s33_path.py", "--base-url", base]
m.main()
