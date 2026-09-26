"""L140: сервис :8094 с мобильной сборкой (out/static_mobile) вместо service/static_v2. Запуск из корня репо:
.venv\Scripts\python.exe reports/qa/mobile_serve.py [--port 8094]"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402
import service.app as m  # noqa: E402

m.STATIC = ROOT / "out" / "static_mobile"
port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8094
app = m.create_app("service/data")
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
