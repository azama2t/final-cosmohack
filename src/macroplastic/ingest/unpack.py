"""Safe archive extraction (zip / tar / tar.gz / tar.bz2 / tar.xz / 7z if a tool is available).

Nothing from the archive is ever executed. Every member is validated BEFORE anything is written:
  * no absolute paths, drive letters, UNC paths, '..' components (zip-slip / path traversal);
  * no Windows-reserved names (con, prn, aux, nul, com1-9, lpt1-9, with or without extension),
    no ':' (alternate data streams), no control characters, no trailing dot/space in a component;
  * tar: only regular files and directories (symlinks, hardlinks, devices, fifos -> refused);
    zip: symlink entries (unix mode S_IFLNK) -> refused;
  * limits: number of members, total uncompressed size, per-member compression ratio (zip bomb);
    the byte count is also enforced while streaming (declared sizes may lie);
  * case-insensitive duplicates (A.tif vs a.tif would overwrite each other on Windows) -> refused;
  * final check: resolved destination path is inside the output folder.
Any violation raises UnsafeArchiveError and the archive is not extracted (partial output is removed).
Nested archives (zip inside zip ...) are extracted next to themselves with the same checks, depth <= 2.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".7z")
_RESERVED = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {f"com{i}" for i in range(1, 10)} | {
    f"lpt{i}" for i in range(1, 10)}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class UnsafeArchiveError(RuntimeError):
    pass


@dataclass
class Limits:
    max_files: int = 200_000
    max_total_bytes: int = 60 * 1024 ** 3  # 60 GB uncompressed
    max_ratio: float = 200.0  # per member, only checked for members > 10 MB uncompressed
    max_depth: int = 2  # nested archives


@dataclass
class UnpackResult:
    root: Path
    kind: str
    n_files: int = 0
    n_bytes: int = 0
    nested: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"root": str(self.root), "kind": self.kind, "n_files": self.n_files, "n_bytes": self.n_bytes,
                "nested": self.nested, "skipped": self.skipped, "notes": self.notes}


def archive_kind(path: Path) -> str | None:
    n = path.name.lower()
    for suf, kind in ((".tar.gz", "tar"), (".tgz", "tar"), (".tar.bz2", "tar"), (".tbz2", "tar"), (".tar.xz", "tar"),
                      (".txz", "tar"), (".tar", "tar"), (".zip", "zip"), (".7z", "7z")):
        if n.endswith(suf):
            return kind
    if path.is_file():  # sniff magic
        with open(path, "rb") as f:
            head = f.read(262)
        if head[:4] == b"PK\x03\x04":
            return "zip"
        if head[:6] == b"7z\xbc\xaf\x27\x1c":
            return "7z"
        if len(head) >= 262 and head[257:262] == b"ustar":
            return "tar"
        if head[:2] == b"\x1f\x8b":
            return "tar"  # gz: tarfile will refuse if it is not a tar
    return None


def check_member_name(name: str) -> PurePosixPath:
    """Validate an archive member name, return the normalised relative posix path. Raises UnsafeArchiveError."""
    if name is None or name == "":
        raise UnsafeArchiveError("empty member name")
    n = name.replace("\\", "/")
    if n.startswith("/") or n.startswith("//") or re.match(r"^[A-Za-z]:", n):
        raise UnsafeArchiveError(f"absolute path in archive: {name!r}")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafeArchiveError(f"empty path: {name!r}")
    for p in parts:
        if p == "..":
            raise UnsafeArchiveError(f"path traversal ('..') in archive: {name!r}")
        if _BAD_CHARS.search(p):
            raise UnsafeArchiveError(f"forbidden character in {name!r}")
        if p.endswith(".") or p.endswith(" "):
            raise UnsafeArchiveError(f"component ends with dot/space (Windows ambiguity): {name!r}")
        if p.split(".")[0].lower() in _RESERVED:
            raise UnsafeArchiveError(f"Windows reserved name in {name!r}")
    return PurePosixPath(*parts)


def _inside(dest: Path, rel: PurePosixPath) -> Path:
    target = (dest / Path(*rel.parts)).resolve()
    d = dest.resolve()
    if os.path.commonpath([str(target), str(d)]) != str(d):
        raise UnsafeArchiveError(f"member escapes the output folder: {rel}")
    return target


def _check_dupes(names: list[PurePosixPath]) -> None:
    seen = {}
    for n in names:
        k = str(n).lower()
        if k in seen and seen[k] != str(n):
            raise UnsafeArchiveError(f"case-insensitive duplicate names: {seen[k]!r} vs {str(n)!r}")
        seen[k] = str(n)


def _copy_limited(src, dst_path: Path, budget: list, member: str) -> int:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(dst_path, "wb") as out:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            n += len(chunk)
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise UnsafeArchiveError(f"total uncompressed size limit exceeded while writing {member}")
            out.write(chunk)
    return n


# --------------------------------------------------------------------------- zip
def _plan_zip(zf: zipfile.ZipFile, lim: Limits) -> list:
    infos = zf.infolist()
    if len(infos) > lim.max_files:
        raise UnsafeArchiveError(f"{len(infos)} members > limit {lim.max_files}")
    plan, total = [], 0
    for zi in infos:
        rel = check_member_name(zi.filename)
        mode = (zi.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise UnsafeArchiveError(f"symlink in zip: {zi.filename!r}")
        if zi.flag_bits & 0x1:
            raise UnsafeArchiveError(f"encrypted member (password needed): {zi.filename!r}")
        total += zi.file_size
        if zi.file_size > 10e6 and zi.compress_size > 0 and zi.file_size / zi.compress_size > lim.max_ratio:
            raise UnsafeArchiveError(f"suspicious compression ratio {zi.file_size / zi.compress_size:.0f} "
                                     f"(zip bomb?) for {zi.filename!r}")
        plan.append((zi, rel, zi.is_dir()))
    if total > lim.max_total_bytes:
        raise UnsafeArchiveError(f"declared uncompressed size {total / 1e9:.1f} GB > limit")
    _check_dupes([r for _, r, _ in plan])
    return plan


def _extract_zip(path: Path, dest: Path, lim: Limits, res: UnpackResult) -> None:
    with zipfile.ZipFile(path) as zf:
        plan = _plan_zip(zf, lim)
        targets = [(zi, _inside(dest, rel), d) for zi, rel, d in plan]  # validate all before writing
        budget = [lim.max_total_bytes]
        for zi, tgt, is_dir in targets:
            if is_dir:
                tgt.mkdir(parents=True, exist_ok=True)
                continue
            with zf.open(zi) as src:
                res.n_bytes += _copy_limited(src, tgt, budget, zi.filename)
            res.n_files += 1


# --------------------------------------------------------------------------- tar
def _extract_tar(path: Path, dest: Path, lim: Limits, res: UnpackResult) -> None:
    with tarfile.open(path, "r:*") as tf:
        members = tf.getmembers()
        if len(members) > lim.max_files:
            raise UnsafeArchiveError(f"{len(members)} members > limit {lim.max_files}")
        plan, total = [], 0
        for m in members:
            rel = check_member_name(m.name)
            if not (m.isfile() or m.isdir()):
                kind = "symlink" if m.issym() else "hardlink" if m.islnk() else "device/fifo/other"
                raise UnsafeArchiveError(f"{kind} in tar: {m.name!r}")
            total += m.size
            plan.append((m, rel))
        if total > lim.max_total_bytes:
            raise UnsafeArchiveError(f"declared uncompressed size {total / 1e9:.1f} GB > limit")
        _check_dupes([r for _, r in plan])
        targets = [(m, _inside(dest, rel)) for m, rel in plan]
        budget = [lim.max_total_bytes]
        for m, tgt in targets:
            if m.isdir():
                tgt.mkdir(parents=True, exist_ok=True)
                continue
            src = tf.extractfile(m)
            if src is None:
                continue
            with src:
                res.n_bytes += _copy_limited(src, tgt, budget, m.name)
            res.n_files += 1


# --------------------------------------------------------------------------- 7z
def find_7z() -> str | None:
    for c in (shutil.which("7z"), shutil.which("7za"), r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if c and Path(c).is_file():
            return c
    return None


def _extract_7z(path: Path, dest: Path, lim: Limits, res: UnpackResult) -> None:
    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(path, "r") as z:
            infos = z.list()
            if len(infos) > lim.max_files:
                raise UnsafeArchiveError(f"{len(infos)} members > limit")
            total = 0
            rels = []
            for i in infos:
                rel = check_member_name(i.filename)
                if getattr(i, "is_symlink", False):
                    raise UnsafeArchiveError(f"symlink in 7z: {i.filename!r}")
                _inside(dest, rel)
                total += int(i.uncompressed or 0)
                rels.append(rel)
            if total > lim.max_total_bytes:
                raise UnsafeArchiveError("declared uncompressed size > limit")
            _check_dupes(rels)
            z.extractall(path=dest)
    except ImportError:
        exe = find_7z()
        if not exe:
            raise UnsafeArchiveError("7z archive: neither py7zr nor 7-Zip (7z.exe) available -- "
                                     "unpack manually with 7-Zip and pass the folder") from None
        lst = subprocess.run([exe, "l", "-slt", "-ba", str(path)], capture_output=True, text=True, check=True).stdout
        names, total, rels = [], 0, []
        cur = {}
        for line in lst.splitlines() + [""]:
            if not line.strip():
                if cur.get("Path"):
                    names.append(cur)
                cur = {}
                continue
            if " = " in line:
                k, v = line.split(" = ", 1)
                cur[k.strip()] = v
        if len(names) > lim.max_files:
            raise UnsafeArchiveError(f"{len(names)} members > limit")
        for c in names:
            rel = check_member_name(c["Path"])
            attr = c.get("Attributes", "")
            if re.search(r"(^|\s)l[rwx-]{9}", attr):
                raise UnsafeArchiveError(f"symlink in 7z: {c['Path']!r}")
            _inside(dest, rel)
            total += int(c.get("Size") or 0)
            rels.append(rel)
        if total > lim.max_total_bytes:
            raise UnsafeArchiveError("declared uncompressed size > limit")
        _check_dupes(rels)
        # -snl- : do not store/restore symlinks; -y: assume yes; nothing is executed
        subprocess.run([exe, "x", "-y", f"-o{dest}", str(path)], capture_output=True, check=True)
    for p in dest.rglob("*"):
        if p.is_symlink():
            raise UnsafeArchiveError(f"symlink appeared after 7z extraction: {p}")
        if p.is_file():
            res.n_files += 1
            res.n_bytes += p.stat().st_size


# --------------------------------------------------------------------------- public
def safe_extract(archive: str | os.PathLike, dest: str | os.PathLike, limits: Limits | None = None,
                 _depth: int = 0) -> UnpackResult:
    """Extract `archive` into `dest` (created). Raises UnsafeArchiveError on any violation (dest cleaned)."""
    lim = limits or Limits()
    archive, dest = Path(archive), Path(dest)
    kind = archive_kind(archive)
    if kind is None:
        raise UnsafeArchiveError(f"not a supported archive: {archive.name} (zip, tar[.gz|.bz2|.xz], 7z)")
    existed = dest.exists() and any(dest.iterdir())
    dest.mkdir(parents=True, exist_ok=True)
    res = UnpackResult(root=dest, kind=kind)
    try:
        if kind == "zip":
            _extract_zip(archive, dest, lim, res)
        elif kind == "tar":
            try:
                _extract_tar(archive, dest, lim, res)
            except tarfile.ReadError as e:
                raise UnsafeArchiveError(f"cannot read as tar: {e}") from None
        else:
            _extract_7z(archive, dest, lim, res)
    except (UnsafeArchiveError, zipfile.BadZipFile) as e:
        if not existed:
            shutil.rmtree(dest, ignore_errors=True)
        if isinstance(e, zipfile.BadZipFile):
            raise UnsafeArchiveError(f"bad zip: {e}") from None
        raise
    # nested archives
    if _depth < lim.max_depth:
        for p in sorted(dest.rglob("*")):
            if p.is_file() and archive_kind(p) and p.suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".7z", ".bz2",
                                                                        ".xz", ".tbz2", ".txz"):
                sub = p.with_name(p.name.split(".")[0] + "__unpacked")
                try:
                    r = safe_extract(p, sub, lim, _depth + 1)
                    res.nested.append({"archive": str(p.relative_to(dest)), "to": str(sub.relative_to(dest)),
                                       "n_files": r.n_files})
                    res.n_files += r.n_files
                    res.n_bytes += r.n_bytes
                except UnsafeArchiveError as e:
                    res.skipped.append({"archive": str(p.relative_to(dest)), "reason": str(e)})
    return res


def prepare_input(src: str | os.PathLike, out: Path, limits: Limits | None = None) -> UnpackResult:
    """Folder -> used in place (read-only). Archive -> extracted to out/unpacked (re-used if already there)."""
    src = Path(src)
    if src.is_dir():
        r = UnpackResult(root=src.resolve(), kind="folder")
        for p in src.rglob("*"):
            if p.is_file():
                r.n_files += 1
                r.n_bytes += p.stat().st_size
        r.notes.append("folder used in place (read-only), nothing copied")
        return r
    if not src.is_file():
        raise FileNotFoundError(src)
    dest = out / "unpacked"
    marker = dest / ".ingest_done"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == f"{src.resolve()}|{src.stat().st_size}":
        r = UnpackResult(root=dest, kind=archive_kind(src) or "?")
        r.notes.append("already unpacked earlier (marker matches), re-used")
        for p in dest.rglob("*"):
            if p.is_file() and p != marker:
                r.n_files += 1
                r.n_bytes += p.stat().st_size
    else:
        if dest.exists():
            shutil.rmtree(dest)
        r = safe_extract(src, dest, limits)
        marker.write_text(f"{src.resolve()}|{src.stat().st_size}", encoding="utf-8")
    # a single top-level folder -> descend (typical "dataset.zip/dataset/...")
    entries = [p for p in dest.iterdir() if p.name != ".ingest_done"]
    if len(entries) == 1 and entries[0].is_dir():
        r.notes.append(f"single top-level folder '{entries[0].name}' -> used as root")
        r.root = entries[0]
    return r
