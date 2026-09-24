"""Dataset loaders (MARIDA, later MADOS)."""
from .marida import (  # noqa: F401
    BAND_NAMES,
    CLASS_NAMES,
    CONF_NAMES,
    iter_split,
    list_patches,
    load_patch,
    merge_water,
    parse_scene,
    resolve_root,
)
