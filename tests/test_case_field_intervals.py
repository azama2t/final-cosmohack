"""§28 А (Г1-2): интервал профиля S2 «между событиями» — scripts/case/field_interval_check.py."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fic", ROOT / "scripts" / "case" / "field_interval_check.py")
fic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fic)

pytestmark = pytest.mark.skipif(not fic.SAMPLES.exists() or not fic.TEST.exists(), reason="нет CSV кейса")


def test_split_and_garwood_matches_published():
    t = fic.load()
    assert len(t) == 63 and (t.split == "test").sum() == 14
    c, lo, hi = fic.garwood(t.N.sum(), t.A.sum())
    assert (round(c, 1), round(lo, 1), round(hi, 1)) == (53.9, 49.9, 58.0)


def test_bootstrap_wider_than_poisson_and_point_unchanged():
    t = fic.load()
    bs = fic.boot_days(t, np.random.default_rng(fic.SEED))
    lo, hi = np.quantile(bs, [.025, .975])
    _, glo, ghi = fic.garwood(t.N.sum(), t.A.sum())
    assert lo < glo and hi > ghi and (hi - lo) > 2 * (ghi - glo)
    nb = fic.nb2_fit(t.N, t.A)
    assert nb["converged"] and nb["alpha"] > 0 and nb["lo95"] < 53.9 < nb["hi95"]


def test_nb2_reduces_to_poisson_scale_on_poisson_data():
    rng = np.random.default_rng(0)
    a = np.full(200, 0.2)
    n = rng.poisson(50 * a)
    nb = fic.nb2_fit(n, a)
    assert abs(nb["C"] - n.sum() / a.sum()) < 1.0 and nb["alpha"] < 0.05
