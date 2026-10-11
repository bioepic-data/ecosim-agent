"""Year-coverage naming for EcoSIM climate forcing NetCDF files.

Climate files carry the forcing years they contain in their name, following
the EcoSIM input convention (e.g. Blodget.clim.2012-2022.nc):

    <SITE_ID>_ecosim_climate_<first>-<last>.nc

The years are read from the file's ``year`` variable after it is written, so
the name always matches the data.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple, Union

PathLike = Union[str, os.PathLike]

# A year range such as 1981-2021 or 1981_2021 at the end of the stem.
YEAR_RANGE_SUFFIX = re.compile(r"[._-]((?:1[5-9]|2[0-9])\d{2})[-_]((?:1[5-9]|2[0-9])\d{2})$")


def netcdf_year_range(path: PathLike) -> Optional[Tuple[int, int]]:
    """First and last value of the ``year`` variable, or None if unavailable."""
    try:
        from netCDF4 import Dataset
        import numpy as np
    except ImportError:
        return None
    with Dataset(str(path)) as dataset:
        if "year" not in dataset.variables:
            return None
        years = np.ma.compressed(np.ma.masked_invalid(dataset.variables["year"][:])).astype(int)
    if years.size == 0:
        return None
    return int(years.min()), int(years.max())


def climate_name_with_years(path: PathLike, first_year: int, last_year: int) -> Path:
    """Return ``path`` with ``_<first>-<last>`` before ``.nc``.

    A name that already ends with the same range (``-`` or ``_`` separated) is
    kept; a different trailing range is replaced.
    """
    path = Path(path)
    stem = path.name[:-3] if path.name.endswith(".nc") else path.name
    suffix = ".nc" if path.name.endswith(".nc") else ""
    match = YEAR_RANGE_SUFFIX.search(stem)
    if match and (int(match.group(1)), int(match.group(2))) == (first_year, last_year):
        return path
    if match:
        stem = stem[: match.start()]
    return path.with_name(f"{stem}_{first_year}-{last_year}{suffix}")


def finalize_climate_file_name(path: PathLike) -> Path:
    """Rename a written climate NetCDF so its name carries its year coverage.

    Returns the final path (unchanged when the years cannot be read or the name
    already matches). An existing file at the target name is replaced.
    """
    path = Path(path)
    years = netcdf_year_range(path)
    if years is None:
        return path
    target = climate_name_with_years(path, *years)
    if target != path:
        os.replace(path, target)
    return target
