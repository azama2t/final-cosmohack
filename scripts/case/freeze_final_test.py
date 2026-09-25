"""Фиксация отложенного финального test концентрации ДО моделей (L68).

Правило (configs/case_selection.yaml: final_test): для каждого профиля дни рейса по порядку режутся
на k_blocks = 5 непрерывных участков маршрута с примерно равным числом событий (splits.route_block_groups);
в test уходит участок sha256("<seed>|<profile>") mod 5 — выбор не зависит от значений C. Дни рейса в
пределах buffer_days = 1 календарных суток от test-дней — буфер (не входят ни в test, ни в dev).
Метки (concentration_items_km2) здесь не читаются и не печатаются.

Выход: data/case/splits/final_test_<profile>.csv (+ копия в reports/case_splits/),
секция final_test в configs/case_selection.yaml (дописывается текстом, комментарии сохраняются).
Повторный запуск проверяет, что состав совпадает с записанным sha256, и ничего не меняет.

    .venv\\Scripts\\python.exe scripts\\case\\freeze_final_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402

from macroplastic.case import selection as S  # noqa: E402
from macroplastic.case import splits as P  # noqa: E402

SEED, K_BLOCKS, BUFFER_DAYS = 42, 5, 1
PROFILES = ["S2_visual_total_plastic", "S1_trawl_total_plastic"]
REPORT_SPLITS = REPO / "reports" / "case_splits"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    d = S.load_samples()
    cfg = S.load_config()
    frozen = cfg.get("final_test")
    entries = {}
    for prof in PROFILES:
        acc, _ = S.select(d, cfg, prof)
        acc = acc.drop(columns=["target"])            # метки не нужны для состава
        ft = P.final_test_split(acc, prof, seed=SEED, k_blocks=K_BLOCKS, buffer_days=BUFFER_DAYS)
        sha_t, sha_d = P.composition_sha256(ft, "test"), P.composition_sha256(ft, "dev")
        if frozen:
            old = frozen["profiles"][prof]
            if old["test_sha256"] != sha_t or old["dev_sha256"] != sha_d:
                sys.exit(f"{prof}: состав не совпадает с зафиксированным sha256 — отказ (test заморожен).")
        name = f"final_test_{prof}"
        path = P.save_split(ft, name)
        REPORT_SPLITS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, REPORT_SPLITS / f"{name}.csv")
        # контроль: 0 общих событий и дней рейса, минимальный интервал и расстояние test ↔ dev
        te, dv = ft.role == "test", ft.role == "dev"
        assert not set(ft.event_id[te]) & set(ft.event_id[dv])
        assert not set(ft.cruise_day[te]) & set(ft.cruise_day[dv])
        t = P.times_days(acc)
        lat, lon = acc.latitude.to_numpy(float), acc.longitude.to_numpy(float)
        ti, di = np.nonzero(te.to_numpy())[0], np.nonzero(dv.to_numpy())[0]
        dt = np.abs(t[ti][:, None] - t[di][None, :]).min()
        dist = P.haversine_km(lat[ti][:, None], lon[ti][:, None], lat[di][None, :], lon[di][None, :]).min()
        entries[prof] = dict(block=int(P.final_test_block_index(prof, SEED, K_BLOCKS)),
                             n_test=int(te.sum()), n_buffer=int((ft.role == "buffer").sum()), n_dev=int(dv.sum()),
                             test_days=f"{ft.date_utc[te].min()}..{ft.date_utc[te].max()}",
                             test_cruise_days=int(ft.cruise_day[te].nunique()),
                             min_dt_days=round(float(dt), 3), min_dist_km=round(float(dist), 1),
                             test_sha256=sha_t, dev_sha256=sha_d, file=f"data/case/splits/{name}.csv")
        print(prof, entries[prof])
    if frozen:
        print("final_test уже зафиксирован в configs/case_selection.yaml, составы совпадают — ничего не меняю.")
        return
    lines = ["", "# Отложенный финальный test концентрации (L68) — зафиксирован ДО моделей концентрации L68,",
             "# метки test не читаются до приёмки 26.09 12:00: единственный читатель — scripts/case/final_test_conc.py",
             "# (однократно, отказ при существующем reports/case_conc/final_test.json). Модели и интервалы",
             "# выбираются только на dev (CV route_buf1 внутри dev). Честная оговорка: L60 смотрел CV по всем",
             "# событиям профиля (в т.ч. будущему test) — но только бейзлайны median/mean/kNN/wind_lin.",
             "final_test:",
             f"  frozen_at: \"{datetime.now().isoformat(timespec='seconds')}\"",
             f"  seed: {SEED}",
             f"  k_blocks: {K_BLOCKS}",
             f"  buffer_days: {BUFFER_DAYS}",
             "  rule: \"дни рейса по порядку → k_blocks непрерывных участков с ~равным числом событий "
             "(splits.route_block_groups); test = участок sha256('<seed>|<profile>') mod k_blocks; "
             "буфер = дни рейса в пределах buffer_days кал. суток от test-дней (не test и не dev); "
             "sha256 = sha256 отсортированных строк 'sample_id,event_id'\"",
             "  not_before: \"2026-09-26T12:00\"",
             "  result: reports/case_conc/final_test.json",
             "  profiles:"]
    for prof, e in entries.items():
        lines.append(f"    {prof}:")
        for k, v in e.items():
            lines.append(f"      {k}: " + (f"\"{v}\"" if isinstance(v, str) else f"{v}"))
    with open(S.CONFIG_YAML, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("секция final_test дописана в", S.CONFIG_YAML)


if __name__ == "__main__":
    main()
