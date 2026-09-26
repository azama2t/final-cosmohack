"""§56 (L151): OUR photo counter (the same weights as «Счёт по фото», no retraining) on every frame of «Дроны»
-> data/case/drones/pred.json (compact, in git). Batch in advance, CPU (CUDA_VISIBLE_DEVICES=-1).

Model per set = the survey the «Счёт по фото» tab would use: nadir drone / aircraft frames -> "aerial"
(model_card_aerial.json, trained on Winans 2023 train split); FML (camera on a vessel) -> "water_camera"
(model_card.json, trained on FML train sessions). Input = the original file (as a user would upload it).
Threshold = the model card's threshold (chosen on val). Matching to the set's labels: greedy by score, IoU >= 0.5
(boxes; TUN polygons -> their bounding box). The model has one class («предмет») — no material, no algae.

Run: CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/case/drones_pred.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.photo_count import metrics as M  # noqa: E402
from macroplastic.photo_count.model import load_card, load_model, predict_images  # noqa: E402

IDX = ROOT / "data" / "case" / "drones" / "index.json"
OUT = ROOT / "data" / "case" / "drones" / "pred.json"
IOU = 0.5
SURVEY = {"fml": "water_camera"}  # everything else: aerial (nadir)
TRAINED = {
    "winans2023": "счётчик обучался на этом наборе (Winans 2023, train); показанные кадры — из отложенного test",
    "fml": "счётчик обучался на этом наборе (FML, train-сессии); показанные кадры — из отложенных test-сессий",
}
NOT_TRAINED = "счётчик не обучался на этом наборе (перенос без дообучения)"


def main():
    idx = json.loads(IDX.read_text(encoding="utf-8"))
    fn = json.loads((ROOT / "reports" / "final_numbers.json").read_text(encoding="utf-8"))["case"]["sections"]["photo_count"]
    models, cards = {}, {}
    for sv in ("aerial", "water_camera"):
        c = load_card(sv)
        cards[sv] = c
        models[sv] = load_model(Path(c["_dir"]) / c["weights_file"], "cpu")
    out_sets = {}
    for s in idx["sets"]:
        sid = s["meta"]["id"]
        sv = SURVEY.get(sid, "aerial")
        card, model = cards[sv], models[sv]
        thr = float(card["threshold"])
        frames = {}
        tp_s = fp_s = gt_s = n_s = 0
        has_gt = True
        for f in s["frames"]:
            p = ROOT / f["src"]
            with Image.open(p) as im:
                im = im.convert("RGB")
                W, H = im.size
                b, sc = predict_images(model, [im], "cpu", 1)[0]
            keep = sc >= thr
            b, sc = b[keep], sc[keep]
            nb = np.column_stack([b[:, 0] / W, b[:, 1] / H, (b[:, 2] - b[:, 0]) / W, (b[:, 3] - b[:, 1]) / H]) if len(b) else np.zeros((0, 4))
            rec = {"n": int(len(b)), "boxes": [[round(float(v), 4) for v in r] for r in nb],
                   "scores": [round(float(v), 3) for v in sc],
                   "conf_mean": round(float(sc.mean()), 3) if len(sc) else None,
                   "conf_min": round(float(sc.min()), 3) if len(sc) else None}
            n_s += rec["n"]
            if f["objects"] is None:
                has_gt = False
            else:
                gt = np.array([o["bbox"] for o in f["objects"]], float).reshape(-1, 4)
                gt_xyxy = np.column_stack([gt[:, 0], gt[:, 1], gt[:, 0] + gt[:, 2], gt[:, 1] + gt[:, 3]]) if len(gt) else np.zeros((0, 4))
                pr_xyxy = np.column_stack([nb[:, 0], nb[:, 1], nb[:, 0] + nb[:, 2], nb[:, 1] + nb[:, 3]]) if len(nb) else np.zeros((0, 4))
                _, is_tp = M.match_image(pr_xyxy, sc, gt_xyxy, IOU)
                order = np.argsort(-sc, kind="stable")
                tp_mask = np.zeros(len(sc), bool)
                tp_mask[order[is_tp]] = True
                rec.update(tp=int(tp_mask.sum()), fp=int((~tp_mask).sum()), fn=int(len(gt) - tp_mask.sum()), n_labelled=int(len(gt)),
                           match=[1 if t else 0 for t in tp_mask])
                tp_s += rec["tp"]
                fp_s += rec["fp"]
                gt_s += len(gt)
            frames[f["id"]] = rec
            print(sid, f["id"], rec["n"], rec.get("n_labelled"), flush=True)
        checked = None
        if sid == "winans2023":
            checked = {"count_mae_per_frame": fn["area"]["count_mae"], "n_test_frames": fn["area"]["n_test_frames"],
                       "source": "reports/final_numbers.json case.sections.photo_count.area"}
        elif sid == "fml":
            checked = {"count_mae_per_frame": fn["frame"]["mae"], "n_test_frames": fn["frame"]["n_images"],
                       "source": "reports/final_numbers.json case.sections.photo_count.frame"}
        elif sid == "maharjan2022":
            checked = None  # transfer of the FML model was AP50 0.01 (final_numbers transfer); aerial model here: see summary
        out_sets[sid] = {
            "survey": sv, "model": card.get("version"), "threshold": thr, "iou": IOU,
            "trained_on_this_set": sid in TRAINED, "training_note": TRAINED.get(sid, NOT_TRAINED),
            "checked_metric": checked,
            "summary": {"frames": len(s["frames"]), "n_pred": n_s,
                        **({"found": tp_s, "labelled": gt_s, "false": fp_s,
                            "recall": round(tp_s / gt_s, 3) if gt_s else None,
                            "precision": round(tp_s / n_s, 3) if n_s else None} if has_gt else
                           {"found": None, "labelled": None, "false": None,
                            "note": "рамок в наборе нет — сравнить с разметкой нельзя, только число найденного моделью"})},
            "frames": frames}
        print("==", sid, out_sets[sid]["summary"], flush=True)
    tot = {k: sum((v["summary"].get(k) or 0) for v in out_sets.values()) for k in ("found", "labelled", "false", "n_pred")}
    res = {"version": 1, "built": dt.datetime.now().isoformat(timespec="seconds"), "builder": "scripts/case/drones_pred.py",
           "device": "cpu", "classes": "одна: «предмет» — модель не определяет материал; водоросли не размечены и не выдумываются",
           "match_rule": f"жадно по уверенности, IoU ≥ {IOU} с рамкой разметки (полигон → его рамка)",
           "total": tot, "sets": out_sets}
    OUT.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("TOTAL", tot, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
