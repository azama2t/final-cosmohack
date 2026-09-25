"""LightGBM predictor: pixel LightGBM for Marine Debris (trained on MARIDA by scripts/train_lgbm.py).

    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor()                       # weights/lgbm/{model.txt, meta.json}
    prob = pred.predict_proba(arr, channel_names)  # arr (C,H,W) float32 reflectance 0..1 -> (H,W) float32 P(MD)
    mask = prob >= pred.threshold
    probs = pred.predict_proba_many([(arr1, names1), (arr2, names2)])   # many chips, one model call
    # live Sen2Cor L2A scenes (domain shift vs ACOLITE rhorc): optional per-scene water-median harmonization
    pred = load_predictor(harmonize="water_median"); prob = pred.predict_proba(arr, names, water_mask=scl == 6)

The 11 MARIDA bands are picked by name (extra bands such as B9 of L2A are ignored). Pixels with
any NaN/inf band -> probability 0. Large tiles are processed in 512 px blocks with a halo
(memory ~ a few hundred MB for 2500x2500). Registered in the model registry as 'lgbm'.
Domain note: trained on ACOLITE rhorc (L1C, Rayleigh-corrected); on Sen2Cor L2A the reflectance
levels differ (see reports/l3_lgbm.md), so the probability is not calibrated there.

Speed (speed-up, reports/speed.md):
  * CPU backend calls lib_lightgbm directly through ctypes (LGBM_BoosterPredictForMat, the same C
    function lightgbm.Booster.predict uses) - identical output, but no `import lightgbm` (which pulls
    pandas + scikit-learn, ~2 s of a cold start).
  * device='cuda' / 'auto': the forest (model.txt) is evaluated by a small CUDA kernel compiled at
    load time with NVRTC and launched through the CUDA driver API (ctypes; no torch import). The
    kernel reproduces LightGBM's numerical decision exactly (float32 feature vs threshold rounded
    down to float32 == double compare; NaN/zero missing handling; double sum in tree order; sigmoid),
    so probabilities equal the CPU ones up to float rounding of exp(). Any CUDA/NVRTC problem ->
    warning and the CPU backend. device=None/'cpu' (library default) = CPU.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

from ..features.pixel import BANDS11, compute_features, compute_features_blocked, feature_names, select_bands

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEIGHTS = _ROOT / "weights" / "lgbm"
# MARIDA train "Marine Water" (class 7) per-band medians, used if meta.json has no water_ref
_WATER_REF_FALLBACK = [0.0368, 0.0342, 0.0268, 0.0175, 0.0142, 0.014, 0.0148, 0.0121, 0.014, 0.0086, 0.0062]


def _log():
    from ..utils import get_logger

    return get_logger("macroplastic.models")


def _threads() -> int:
    try:
        from ..utils import available_cpus

        return available_cpus()
    except Exception:
        return os.cpu_count() or 4


def _pkg_dir(name: str) -> Path | None:
    """Directory of an installed package without importing it."""
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        return None
    if spec is None:
        return None
    locs = list(spec.submodule_search_locations or [])
    if locs:
        return Path(locs[0])
    return Path(spec.origin).parent if spec.origin else None


# ----------------------------------------------------------------------------- CPU: lib_lightgbm via ctypes

class _CBooster:
    """Minimal ctypes wrapper of lib_lightgbm: load model.txt, predict dense float32 rows.

    Same C calls as lightgbm.Booster(model_file=...).predict(X, num_threads=n) (num_iteration=-1,
    normal prediction), so the output is bit-identical, without importing the lightgbm package.
    """

    def __init__(self, model_file: Path):
        d = _pkg_dir("lightgbm")
        if d is None:
            raise ImportError("lightgbm is not installed")
        name = "lib_lightgbm.dll" if sys.platform == "win32" else (
            "lib_lightgbm.dylib" if sys.platform == "darwin" else "lib_lightgbm.so")
        cands = [d / "bin" / name, d / "lib" / name, d.parent / name]
        lib_path = next((p for p in cands if p.is_file()), None)
        if lib_path is None:
            raise ImportError(f"{name} not found next to the lightgbm package")
        self._lib = lib = ctypes.cdll.LoadLibrary(str(lib_path))
        lib.LGBM_GetLastError.restype = ctypes.c_char_p
        self._handle = ctypes.c_void_p()
        n_iter = ctypes.c_int(0)
        self._ck(lib.LGBM_BoosterCreateFromModelfile(str(model_file).encode("utf-8"), ctypes.byref(n_iter),
                                                     ctypes.byref(self._handle)))
        n_cls = ctypes.c_int(0)
        self._ck(lib.LGBM_BoosterGetNumClasses(self._handle, ctypes.byref(n_cls)))
        self.num_class = int(n_cls.value)

    def _ck(self, ret: int) -> None:
        if ret != 0:
            raise RuntimeError(self._lib.LGBM_GetLastError().decode("utf-8", "replace"))

    def predict(self, X: np.ndarray, num_threads: int) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float32)
        n = int(X.shape[0])
        out = np.empty(n * self.num_class, np.float64)
        if n == 0:
            return out
        out_len = ctypes.c_int64(0)
        param = f"num_threads={int(num_threads)}".encode()
        step = 2 ** 31 - 1
        for s in range(0, n, step):  # int32 row count per call (same as lightgbm's chunking)
            xs = X[s:s + step]
            o = out[s * self.num_class:(s + len(xs)) * self.num_class]
            self._ck(self._lib.LGBM_BoosterPredictForMat(
                self._handle, xs.ctypes.data_as(ctypes.c_void_p), ctypes.c_int(0),  # C_API_DTYPE_FLOAT32
                ctypes.c_int32(xs.shape[0]), ctypes.c_int32(xs.shape[1]), ctypes.c_int(1),  # row major
                ctypes.c_int(0), ctypes.c_int(0), ctypes.c_int(-1), ctypes.c_char_p(param),
                ctypes.byref(out_len), o.ctypes.data_as(ctypes.POINTER(ctypes.c_double))))
        if self.num_class > 1:
            return out.reshape(n, self.num_class)
        return out

    def __del__(self):
        try:
            self._lib.LGBM_BoosterFree(self._handle)
        except Exception:
            pass


class _PyBooster:
    """Fallback: the lightgbm Python package (slow import)."""

    def __init__(self, model_file: Path):
        import lightgbm as lgb

        self.booster = lgb.Booster(model_file=str(model_file))

    def predict(self, X: np.ndarray, num_threads: int) -> np.ndarray:
        return self.booster.predict(X, num_threads=num_threads)


# ----------------------------------------------------------------------------- GPU: NVRTC kernel via ctypes

def parse_forest(model_file: Path) -> dict:
    """Parse a LightGBM model.txt (numerical splits only) into flat arrays for the CUDA kernel.

    nodes (M,4) int32: [feature | decision_type << 16, float32 bits of the threshold rounded DOWN
    to float32, left child, right child] (child < 0 -> leaf ~child). For float32 input x and a double
    threshold t:  x <= t  <=>  x <= float32_round_down(t), so the kernel's float compare is exact.
    """
    txt = Path(model_file).read_text(encoding="utf-8")
    head, _, rest = txt.partition("\nTree=")
    hdr = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    obj = hdr.get("objective", "").split()
    if not obj or obj[0] != "binary" or int(hdr.get("num_tree_per_iteration", "1")) != 1:
        raise ValueError(f"CUDA backend supports binary models only (objective={hdr.get('objective')!r})")
    sigmoid = 1.0
    for o in obj[1:]:
        if o.startswith("sigmoid:"):
            sigmoid = float(o.split(":", 1)[1])
    if "average_output" in head.split():
        raise ValueError("average_output (random forest) models are not supported by the CUDA backend")
    body = rest.split("\nend of trees", 1)[0]
    blocks = ("Tree=" + body).split("\nTree=")
    nodes, node_off, leaves, leaf_off = [], [], [], []
    n_nodes = n_leaves = 0
    for blk in blocks:
        kv = dict(line.split("=", 1) for line in blk.splitlines() if "=" in line)
        nl = int(kv["num_leaves"])
        if int(kv.get("num_cat", "0")) > 0:
            raise ValueError("categorical splits are not supported by the CUDA backend")
        if int(kv.get("is_linear", "0")):
            raise ValueError("linear trees are not supported by the CUDA backend")
        lv = np.array([float(v) for v in kv["leaf_value"].split()], np.float64)
        leaf_off.append(n_leaves)
        leaves.append(lv)
        n_leaves += len(lv)
        if nl <= 1:
            node_off.append(-1)
            continue
        feat = np.array(kv["split_feature"].split(), np.int64)
        thr = np.array([float(v) for v in kv["threshold"].split()], np.float64)
        dt = np.array(kv["decision_type"].split(), np.int64)
        if (dt & 1).any():
            raise ValueError("categorical splits are not supported by the CUDA backend")
        t32 = thr.astype(np.float32)
        down = t32.astype(np.float64) > thr
        t32[down] = np.nextafter(t32[down], np.float32(-np.inf))
        nd = np.empty((nl - 1, 4), np.int32)
        nd[:, 0] = (feat & 0xFFFF) | ((dt & 0xFF) << 16)
        nd[:, 1] = t32.view(np.int32)
        nd[:, 2] = np.array(kv["left_child"].split(), np.int32)
        nd[:, 3] = np.array(kv["right_child"].split(), np.int32)
        node_off.append(n_nodes)
        nodes.append(nd)
        n_nodes += nl - 1
    return {
        "nodes": np.ascontiguousarray(np.concatenate(nodes) if nodes else np.zeros((1, 4), np.int32)),
        "node_off": np.asarray(node_off, np.int32),
        "leaves": np.ascontiguousarray(np.concatenate(leaves)),
        "leaf_off": np.asarray(leaf_off, np.int32),
        "sigmoid": sigmoid,
        "n_features": int(hdr.get("max_feature_idx", "-1")) + 1,
    }


_KERNEL = r"""
extern "C" __global__ void forest_predict(const float* __restrict__ X, const long long n, const int nf,
    const int4* __restrict__ nodes, const int* __restrict__ node_off, const double* __restrict__ leaves,
    const int* __restrict__ leaf_off, const int n_trees, const double sigmoid, float* __restrict__ out)
{
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* x = X + i * nf;
    double s = 0.0;
    for (int t = 0; t < n_trees; ++t) {
        const int base = __ldg(&node_off[t]);
        int leaf = 0;
        if (base >= 0) {
            int node = 0;
            while (true) {
                const int4 nd = __ldg(&nodes[base + node]);
                const int dt = (nd.x >> 16) & 0xFF;
                const int mt = (dt >> 2) & 3;          /* 0 none, 1 zero, 2 nan */
                float v = __ldg(&x[nd.x & 0xFFFF]);
                const bool isn = isnan(v);
                /* dense row -> LightGBM keeps only |v| > kZeroThreshold (1e-35f) or NaN; rest is 0 */
                if (!isn && fabsf(v) <= 1e-35f) v = 0.0f;
                if (isn && mt != 2) v = 0.0f;
                bool go_left;
                if ((mt == 1 && v == 0.0f) || (mt == 2 && isn)) go_left = (dt & 2) != 0;
                else go_left = v <= __int_as_float(nd.y);
                node = go_left ? nd.z : nd.w;
                if (node < 0) { leaf = ~node; break; }
            }
        }
        s += __ldg(&leaves[__ldg(&leaf_off[t]) + leaf]);
    }
    out[i] = (float)(1.0 / (1.0 + exp(-sigmoid * s)));
}
"""


def _find_nvrtc() -> list[Path]:
    """Candidate NVRTC libraries: CUDA_PATH, pip nvidia-cuda-nvrtc, torch/lib (found without importing torch)."""
    dirs: list[Path] = []
    for env in ("CUDA_PATH", "CUDA_HOME"):
        if os.environ.get(env):
            dirs += [Path(os.environ[env]) / "bin", Path(os.environ[env]) / "lib64"]
    for pkg, sub in (("nvidia", ("cuda_nvrtc/bin", "cuda_nvrtc/lib")), ("torch", ("lib",))):
        d = _pkg_dir(pkg)
        if d is not None:
            dirs += [d / s for s in sub]
    pat = "nvrtc64_*.dll" if sys.platform == "win32" else "libnvrtc*.so*"
    out = []
    for d in dirs:
        if d.is_dir():
            out += sorted(p for p in d.glob(pat) if ".alt." not in p.name and "builtins" not in p.name)
    return out


class CudaForest:
    """LightGBM binary forest on the GPU: NVRTC-compiled kernel, CUDA driver API through ctypes."""

    BLOCK = 256

    def __init__(self, model_file: Path, device_index: int = 0):
        if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
            raise RuntimeError("CUDA_VISIBLE_DEVICES is empty")
        f = parse_forest(model_file)
        self.n_features = f["n_features"]
        self.n_trees = len(f["node_off"])
        self.sigmoid = float(f["sigmoid"])
        if sys.platform == "win32":
            cu = ctypes.WinDLL("nvcuda.dll")
        else:
            cu = ctypes.CDLL("libcuda.so.1")
        self._cu = cu
        for fn in ("cuMemcpyHtoD_v2", "cuMemcpyDtoH_v2"):
            getattr(cu, fn).argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t] if "HtoD" in fn else \
                [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_size_t]
        cu.cuMemAlloc_v2.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
        cu.cuMemFree_v2.argtypes = [ctypes.c_uint64]
        self._ck(cu.cuInit(0), "cuInit")
        cnt = ctypes.c_int(0)
        self._ck(cu.cuDeviceGetCount(ctypes.byref(cnt)), "cuDeviceGetCount")
        if cnt.value <= device_index:
            raise RuntimeError("no CUDA device")
        dev = ctypes.c_int(0)
        self._ck(cu.cuDeviceGet(ctypes.byref(dev), device_index), "cuDeviceGet")
        major, minor = ctypes.c_int(0), ctypes.c_int(0)
        self._ck(cu.cuDeviceGetAttribute(ctypes.byref(major), 75, dev), "cuDeviceGetAttribute")
        self._ck(cu.cuDeviceGetAttribute(ctypes.byref(minor), 76, dev), "cuDeviceGetAttribute")
        name = ctypes.create_string_buffer(256)
        cu.cuDeviceGetName(name, 256, dev)
        self.device_name = name.value.decode("utf-8", "replace")
        self._ctx = ctypes.c_void_p()
        self._ck(cu.cuDevicePrimaryCtxRetain(ctypes.byref(self._ctx), dev), "cuDevicePrimaryCtxRetain")
        self._ck(cu.cuCtxSetCurrent(self._ctx), "cuCtxSetCurrent")
        image = self._compile(10 * major.value + minor.value)
        self._mod = ctypes.c_void_p()
        self._ck(cu.cuModuleLoadData(ctypes.byref(self._mod), image), "cuModuleLoadData")
        self._fn = ctypes.c_void_p()
        self._ck(cu.cuModuleGetFunction(ctypes.byref(self._fn), self._mod, b"forest_predict"), "cuModuleGetFunction")
        self._keep = [image]
        self._d_nodes = self._upload(f["nodes"])
        self._d_node_off = self._upload(f["node_off"])
        self._d_leaves = self._upload(f["leaves"])
        self._d_leaf_off = self._upload(f["leaf_off"])
        self._cap = 0
        self._d_x = self._d_out = None

    # -- helpers
    def _ck(self, ret: int, what: str) -> None:
        if ret != 0:
            s = ctypes.c_char_p()
            try:
                self._cu.cuGetErrorString(ret, ctypes.byref(s))
            except Exception:
                pass
            raise RuntimeError(f"{what}: CUDA error {ret} {s.value.decode() if s.value else ''}".strip())

    def _upload(self, a: np.ndarray) -> ctypes.c_uint64:
        a = np.ascontiguousarray(a)
        d = ctypes.c_uint64(0)
        self._ck(self._cu.cuMemAlloc_v2(ctypes.byref(d), max(1, a.nbytes)), "cuMemAlloc")
        self._ck(self._cu.cuMemcpyHtoD_v2(d.value, a.ctypes.data, a.nbytes), "cuMemcpyHtoD")
        return d

    def _compile(self, cc: int) -> bytes:
        errs = []
        for path in _find_nvrtc():
            try:
                if sys.platform == "win32":
                    os.add_dll_directory(str(path.parent))
                    for b in sorted(path.parent.glob("nvrtc-builtins64_*.dll")):
                        ctypes.CDLL(str(b))
                nv = ctypes.CDLL(str(path))
                return self._compile_with(nv, cc)
            except Exception as e:  # try the next NVRTC
                errs.append(f"{path.name}: {type(e).__name__}: {e}")
        raise RuntimeError("NVRTC not usable: " + ("; ".join(errs) or "library not found"))

    def _compile_with(self, nv, cc: int) -> bytes:
        archs: list[int] = []
        try:
            n = ctypes.c_int(0)
            if nv.nvrtcGetNumSupportedArchs(ctypes.byref(n)) == 0 and n.value > 0:
                arr = (ctypes.c_int * n.value)()
                nv.nvrtcGetSupportedArchs(arr)
                archs = list(arr)
        except Exception:
            pass
        cubin = not archs or cc in archs
        arch = cc if cubin else max([a for a in archs if a <= cc] or [min(archs)])
        prog = ctypes.c_void_p()
        if nv.nvrtcCreateProgram(ctypes.byref(prog), _KERNEL.encode(), b"forest.cu", 0, None, None) != 0:
            raise RuntimeError("nvrtcCreateProgram failed")
        try:
            opts = [f"--gpu-architecture={'sm' if cubin else 'compute'}_{arch}".encode(), b"--fmad=false"]
            copts = (ctypes.c_char_p * len(opts))(*opts)
            ret = nv.nvrtcCompileProgram(prog, len(opts), copts)
            if ret != 0:
                sz = ctypes.c_size_t(0)
                nv.nvrtcGetProgramLogSize(prog, ctypes.byref(sz))
                buf = ctypes.create_string_buffer(sz.value + 1)
                nv.nvrtcGetProgramLog(prog, buf)
                raise RuntimeError(f"nvrtc compile error {ret}: {buf.value.decode('utf-8', 'replace')[:500]}")
            sz = ctypes.c_size_t(0)
            if cubin:
                nv.nvrtcGetCUBINSize(prog, ctypes.byref(sz))
                buf = ctypes.create_string_buffer(sz.value)
                if nv.nvrtcGetCUBIN(prog, buf) != 0:
                    raise RuntimeError("nvrtcGetCUBIN failed")
            else:
                nv.nvrtcGetPTXSize(prog, ctypes.byref(sz))
                buf = ctypes.create_string_buffer(sz.value)
                if nv.nvrtcGetPTX(prog, buf) != 0:
                    raise RuntimeError("nvrtcGetPTX failed")
            return buf.raw
        finally:
            nv.nvrtcDestroyProgram(ctypes.byref(prog))

    def _ensure(self, n: int) -> None:
        if n <= self._cap:
            return
        cu = self._cu
        for d in (self._d_x, self._d_out):
            if d is not None:
                cu.cuMemFree_v2(d.value)
        cap = max(n, 1 << 20)
        self._d_x, self._d_out = ctypes.c_uint64(0), ctypes.c_uint64(0)
        self._ck(cu.cuMemAlloc_v2(ctypes.byref(self._d_x), cap * self.n_features * 4), "cuMemAlloc")
        self._ck(cu.cuMemAlloc_v2(ctypes.byref(self._d_out), cap * 4), "cuMemAlloc")
        self._cap = cap

    def predict(self, X: np.ndarray) -> np.ndarray:
        """(N, n_features) float32 -> (N,) float32 probability."""
        X = np.ascontiguousarray(X, dtype=np.float32)
        if X.ndim != 2 or X.shape[1] != self.n_features:
            raise ValueError(f"expected (N, {self.n_features}) rows, got {X.shape}")
        n = int(X.shape[0])
        out = np.empty(n, np.float32)
        if n == 0:
            return out
        cu = self._cu
        self._ck(cu.cuCtxSetCurrent(self._ctx), "cuCtxSetCurrent")  # context is per thread
        self._ensure(n)
        self._ck(cu.cuMemcpyHtoD_v2(self._d_x.value, X.ctypes.data, X.nbytes), "cuMemcpyHtoD")
        args = [ctypes.c_uint64(self._d_x.value), ctypes.c_longlong(n), ctypes.c_int(self.n_features),
                ctypes.c_uint64(self._d_nodes.value), ctypes.c_uint64(self._d_node_off.value),
                ctypes.c_uint64(self._d_leaves.value), ctypes.c_uint64(self._d_leaf_off.value),
                ctypes.c_int(self.n_trees), ctypes.c_double(self.sigmoid), ctypes.c_uint64(self._d_out.value)]
        params = (ctypes.c_void_p * len(args))(*[ctypes.cast(ctypes.pointer(a), ctypes.c_void_p) for a in args])
        grid = (n + self.BLOCK - 1) // self.BLOCK
        self._ck(cu.cuLaunchKernel(self._fn, ctypes.c_uint(grid), ctypes.c_uint(1), ctypes.c_uint(1),
                                   ctypes.c_uint(self.BLOCK), ctypes.c_uint(1), ctypes.c_uint(1), ctypes.c_uint(0),
                                   None, params, None), "cuLaunchKernel")
        self._ck(cu.cuMemcpyDtoH_v2(out.ctypes.data, self._d_out.value, out.nbytes), "cuMemcpyDtoH")
        return out


# ----------------------------------------------------------------------------- predictor

class LGBMPredictor:
    def __init__(self, weights_dir: str | os.PathLike = DEFAULT_WEIGHTS, block: int = 512, num_threads: int | None = None,
                 harmonize: str | None = None, device: str | None = None):
        wd = Path(weights_dir)
        if wd.is_file():  # allow passing model.txt directly
            wd = wd.parent
        self.weights_dir = wd
        self.model_file = wd / "model.txt"
        if not self.model_file.is_file():
            raise FileNotFoundError(str(self.model_file))
        self.meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
        self.name = "lgbm"
        self.required_bands = list(BANDS11)  # inference.py checks these per file before featurizing
        self.threshold = float(self.meta["threshold"])
        self.task = self.meta["task"]
        self.classes = list(self.meta["classes"])
        self.level = self.meta["feature_level"]
        self.features = list(self.meta["features"])
        all_names = feature_names(self.level)
        if all_names != self.features:
            # model trained on a subset of the 'win' features
            all_names = feature_names("win")
            self.level = "win"
        self.fidx = [all_names.index(n) for n in self.features]
        self._fidx_identity = self.fidx == list(range(len(all_names)))
        # Robustness fixes: a model on a feature subset (e.g. the light top-20 model) computes ONLY its features
        # (pixel._compute_subset: same code per feature -> same values as the full stack indexed by fidx)
        self._subset = None if self._fidx_identity else list(self.features)
        self.min_px = int(self.meta.get("postprocess_min_px", 0))
        self.block = int(block)
        self.num_threads = num_threads or _threads()
        if harmonize not in (None, "none", "water_median"):
            raise ValueError(f"harmonize must be None or 'water_median', got {harmonize!r}")
        self.harmonize = None if harmonize in (None, "none") else harmonize
        self.water_ref = np.asarray(self.meta.get("water_ref", _WATER_REF_FALLBACK), np.float32)
        self.last_offset = None  # per-band offset applied by the last harmonized call (diagnostics)
        self._cpu = None
        self._gpu = None
        self.device = "cpu"
        self.gpu_error: str | None = None
        dev = (device or "cpu").lower()
        if dev in ("cuda", "auto", "gpu") and self.task == "binary":
            try:
                self._gpu = CudaForest(self.model_file)
                self.device = "cuda"
            except Exception as e:  # noqa: BLE001 - any CUDA/NVRTC problem -> CPU
                self.gpu_error = f"{type(e).__name__}: {e}"
                if dev != "auto":
                    _log().warning("lgbm CUDA backend unavailable (%s) -> CPU", self.gpu_error)
        if self._gpu is None:
            self._cpu_backend()  # load now: model errors surface at load time

    # -- backends
    def _cpu_backend(self):
        if self._cpu is None:
            try:
                self._cpu = _CBooster(self.model_file)
            except Exception as e:  # noqa: BLE001
                _log().debug("ctypes lib_lightgbm unavailable (%s) -> lightgbm package", e)
                self._cpu = _PyBooster(self.model_file)
        return self._cpu

    @property
    def booster(self):
        """lightgbm.Booster (compatibility; imports the lightgbm package)."""
        if not hasattr(self, "_py_booster"):
            import lightgbm as lgb

            self._py_booster = lgb.Booster(model_file=str(self.model_file))
        return self._py_booster

    def _predict_rows(self, X: np.ndarray) -> np.ndarray:
        if self._gpu is not None:
            try:
                return self._gpu.predict(X)
            except Exception as e:  # noqa: BLE001
                self.gpu_error = f"{type(e).__name__}: {e}"
                _log().warning("lgbm CUDA predict failed (%s) -> CPU", self.gpu_error)
                self._gpu = None
                self.device = "cpu"
        p = self._cpu_backend().predict(X, num_threads=self.num_threads)
        if self.task == "multiclass":
            p = p[:, self.classes.index(int(self.meta.get("md_class", 1)))]
        return np.asarray(p, dtype=np.float32)

    def water_offset(self, arr: np.ndarray, channel_names: Sequence[str], water_mask: np.ndarray | None = None):
        """Per-band offset (BANDS11) that moves the scene's open-water median to the MARIDA water median.

        water_mask: optional (H,W) bool (e.g. SCL == 6 and not cloud). Without it a spectral mask is
        used: NDWI(B3,B8) > 0.1, B2 < 0.2, B11 < 0.05 (dark open water, no clouds/land). Returns None if
        fewer than 2000 water pixels.
        """
        b = select_bands(arr, channel_names)
        ok = np.isfinite(b).all(0)
        if water_mask is None:
            with np.errstate(invalid="ignore", divide="ignore"):
                ndwi = (b[2] - b[7]) / (b[2] + b[7])
            water_mask = (ndwi > 0.1) & (b[1] < 0.2) & (b[9] < 0.05)
        m = ok & np.asarray(water_mask, bool)
        if m.sum() < 2000:
            return None
        step = max(1, int(np.sqrt(m.sum() / 200000)))  # subsample big scenes
        med = np.median(b[:, ::step, ::step][:, m[::step, ::step]], axis=1)
        return (self.water_ref - med).astype(np.float32)

    def _harmonized(self, arr, channel_names, water_mask):
        if self.harmonize == "water_median":
            off = self.water_offset(arr, channel_names, water_mask)
            self.last_offset = off
            if off is not None:
                arr = select_bands(arr, channel_names) + off[:, None, None]
                channel_names = list(BANDS11)
        return arr, channel_names

    def predict_proba(self, arr: np.ndarray, channel_names: Sequence[str], water_mask: np.ndarray | None = None) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.ndim != 3:
            raise ValueError(f"expected (C,H,W), got {arr.shape}")
        arr, channel_names = self._harmonized(arr, channel_names, water_mask)
        H, W = arr.shape[1:]
        out = np.zeros((H, W), np.float32)
        for (y0, y1, x0, x1), f in compute_features_blocked(arr, channel_names, self.level, block=self.block,
                                                            features=self._subset):
            F, h, w = f.shape
            X = f.reshape(F, -1).T
            ok = ~np.isnan(X).all(1)  # compute_features sets every feature to NaN where the input is NaN
            p = np.zeros(len(X), np.float32)
            if ok.any():
                p[ok] = self._predict_rows(np.ascontiguousarray(X[ok]))
            out[y0:y1, x0:x1] = p.reshape(h, w)
        bad = ~np.isfinite(select_bands(arr, channel_names)).all(0)
        out[bad] = 0.0
        if self.min_px > 0:
            out = self._postprocess(out)
        return out

    # -- batched API (inference.py): features per chip (thread-safe, GIL-free mostly), one model call per batch
    def is_small(self, arr: np.ndarray) -> bool:
        """True if the image fits in one feature block (no halo tiling) -> can go to prepare_rows()."""
        return arr.ndim == 3 and arr.shape[1] <= self.block and arr.shape[2] <= self.block

    def prepare_rows(self, arr: np.ndarray, channel_names: Sequence[str], water_mask: np.ndarray | None = None) -> dict:
        """Features of one small image -> {'X': (n_ok, F) float32 rows, 'ok': flat bool, 'bad': (H,W) bool, 'shape'}.

        Same numbers as predict_proba() (which tiles only images larger than `block`)."""
        arr = np.asarray(arr)
        if arr.ndim != 3:
            raise ValueError(f"expected (C,H,W), got {arr.shape}")
        arr, channel_names = self._harmonized(arr, channel_names, water_mask)
        f = compute_features(arr, channel_names, self.level, features=self._subset)
        F, h, w = f.shape
        X = f.reshape(F, -1).T
        ok = ~np.isnan(f).all(0).ravel()  # == ~isnan(X).all(1), without the strided pass
        Xok = np.ascontiguousarray(X) if ok.all() else np.ascontiguousarray(X[ok])
        bad = ~np.isfinite(select_bands(arr, channel_names)).all(0)
        return {"X": Xok, "ok": ok, "bad": bad, "shape": (h, w)}

    def finish_rows(self, rows: dict, p: np.ndarray) -> np.ndarray:
        """Row probabilities of prepare_rows() -> (H,W) float32 probability (bad pixels 0, postprocess)."""
        h, w = rows["shape"]
        ok = rows["ok"]
        if ok.all():
            out = np.asarray(p, np.float32).reshape(h, w).copy()
        else:
            full = np.zeros(h * w, np.float32)
            full[ok] = p
            out = full.reshape(h, w)
        out[rows["bad"]] = 0.0
        if self.min_px > 0:
            out = self._postprocess(out)
        return out

    def predict_rows_many(self, rows_list: list[dict]) -> list[np.ndarray]:
        """One model call for many prepare_rows() results -> list of (H,W) probabilities."""
        if not rows_list:
            return []
        X = rows_list[0]["X"] if len(rows_list) == 1 else np.concatenate([r["X"] for r in rows_list])
        p = self._predict_rows(X) if len(X) else np.zeros(0, np.float32)
        outs, s = [], 0
        for r in rows_list:
            n = len(r["X"])
            outs.append(self.finish_rows(r, p[s:s + n]))
            s += n
        return outs

    def predict_proba_many(self, items: Sequence[tuple], batch_rows: int = 2_000_000) -> list[np.ndarray]:
        """[(arr, channel_names), ...] -> list of (H,W) probabilities; small chips are batched."""
        outs: list = [None] * len(items)
        pend: list[tuple[int, dict]] = []
        n_rows = 0

        def flush():
            nonlocal pend, n_rows
            for (i, _), p in zip(pend, self.predict_rows_many([r for _, r in pend])):
                outs[i] = p
            pend, n_rows = [], 0

        for i, (arr, names) in enumerate(items):
            if self.is_small(np.asarray(arr)):
                r = self.prepare_rows(arr, names)
                pend.append((i, r))
                n_rows += len(r["X"])
                if n_rows >= batch_rows:
                    flush()
            else:
                outs[i] = self.predict_proba(arr, names)
        flush()
        return outs

    def _postprocess(self, prob: np.ndarray) -> np.ndarray:
        """Zero out MD components (prob >= threshold) smaller than min_px pixels (8-connectivity)."""
        from scipy import ndimage

        m = prob >= self.threshold
        lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
        if n:
            sizes = np.bincount(lab.ravel())
            small = sizes < self.min_px
            small[0] = False
            prob = prob.copy()
            prob[small[lab]] = np.minimum(prob[small[lab]], self.threshold * 0.999)
        return prob


def load_predictor(weights_dir: str | os.PathLike | None = None, weights: str | os.PathLike | None = None,
                   device: str | None = None, **kw) -> LGBMPredictor:
    """Registry loader. `weights` (dir or model.txt) is an alias of `weights_dir`.

    device: None/'cpu' -> lib_lightgbm on CPU; 'cuda' -> NVRTC forest kernel (warning + CPU if it
    cannot start); 'auto' -> CUDA if it starts, else CPU silently."""
    wd = weights_dir or weights or DEFAULT_WEIGHTS
    return LGBMPredictor(wd, device=device,
                         **{k: v for k, v in kw.items() if k in ("block", "num_threads", "harmonize")})


try:  # register in the model registry if available
    from .registry import register as _register

    _register("lgbm", load_predictor)
except Exception:  # pragma: no cover
    pass
