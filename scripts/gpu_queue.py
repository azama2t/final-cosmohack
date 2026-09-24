"""Single-GPU job queue. One runner executes jobs one by one; exit codes go to out/gpu_queue.log.

  python scripts/gpu_queue.py submit --name unet_s0 [--wait] -- .venv/Scripts/python.exe train_unet.py --seed 0
  python scripts/gpu_queue.py run        # runner loop (the orchestrator starts one in background)
  python scripts/gpu_queue.py status

Job stdout/stderr: out/gpu_queue/logs/<id>_<name>.log. Job result: out/gpu_queue/done/<id>_<name>.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "out" / "gpu_queue"
PENDING, RUNNING, DONE, LOGS = Q / "pending", Q / "running", Q / "done", Q / "logs"
LOG = ROOT / "out" / "gpu_queue.log"
for d in (PENDING, RUNNING, DONE, LOGS):
    d.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def submit(name, cmd, wait, cwd):
    jid = f"{time.time_ns() // 1_000_000}"
    job = {"id": jid, "name": name, "cmd": cmd, "cwd": cwd or str(ROOT), "submitted": datetime.now().isoformat()}
    stem = f"{jid}_{name}"
    (PENDING / f"{stem}.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    print(f"submitted {stem}; log: {LOGS / (stem + '.log')}")
    if not wait:
        return 0
    while not (DONE / f"{stem}.json").exists():
        time.sleep(5)
    res = json.loads((DONE / f"{stem}.json").read_text(encoding="utf-8"))
    print(f"done {stem}: exit={res['exit']} minutes={res['minutes']}")
    return res["exit"]


def run():
    lock = Q / "runner.pid"
    if lock.exists():
        try:
            pid = int(lock.read_text())
            os.kill(pid, 0)
            print(f"runner already alive pid={pid}")
            return 1
        except (OSError, ValueError):
            pass
    lock.write_text(str(os.getpid()))
    log(f"runner start pid={os.getpid()}")
    for stale in RUNNING.glob("*.json"):  # a job interrupted by a crash goes back to the queue
        stale.rename(PENDING / stale.name)
        log(f"requeued stale {stale.stem}")
    while True:
        jobs = sorted(PENDING.glob("*.json"))
        if not jobs:
            time.sleep(3)
            continue
        jf = jobs[0]
        rf = RUNNING / jf.name
        jf.rename(rf)
        job = json.loads(rf.read_text(encoding="utf-8"))
        stem = jf.stem
        log(f"START {stem}: {' '.join(job['cmd'])}")
        t0 = time.time()
        env = dict(os.environ)
        env.pop("CUDA_VISIBLE_DEVICES", None)
        with (LOGS / f"{stem}.log").open("w", encoding="utf-8") as lf:
            try:
                code = subprocess.call(job["cmd"], cwd=job["cwd"], stdout=lf, stderr=subprocess.STDOUT, env=env)
            except Exception as e:  # bad command line
                lf.write(f"runner error: {e}\n")
                code = 127
        mins = round((time.time() - t0) / 60, 2)
        job.update({"exit": code, "minutes": mins, "finished": datetime.now().isoformat()})
        (DONE / jf.name).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        rf.unlink()
        log(f"END {stem}: exit={code} minutes={mins}")


def status():
    print("running:", [p.stem for p in RUNNING.glob("*.json")])
    print("pending:", [p.stem for p in sorted(PENDING.glob("*.json"))])
    if LOG.exists():
        print("".join(LOG.read_text(encoding="utf-8").splitlines(True)[-10:]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--name", required=True)
    s.add_argument("--wait", action="store_true")
    s.add_argument("--cwd", default=None)
    s.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("run")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "submit":
        rest = a.rest[1:] if a.rest and a.rest[0] == "--" else a.rest
        sys.exit(submit(a.name, rest, a.wait, a.cwd))
    sys.exit(run() if a.cmd == "run" else status())
