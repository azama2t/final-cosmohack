r"""Entry point: .venv\Scripts\python.exe -m service [--port 8000] [--host 127.0.0.1] [--data-root PATH]"""
from __future__ import annotations

import argparse
import os


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m service", description="Макропластик: веб-сервис (API + карта)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-root", default=None,
                    help="корень данных (manifest.json); иначе $MACROPLASTIC_DATA, service/data, service/demo, "
                         "service/demo_fixtures")
    a = ap.parse_args(argv)

    import uvicorn

    from .app import create_app
    from .core import Store

    app = create_app(a.data_root)
    st = Store.open(a.data_root)
    print(f"[service] data root: {st.root} (kind={st.data_kind()}, regions={len(st.regions())})")
    print(f"[service] http://{a.host}:{a.port}/")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
