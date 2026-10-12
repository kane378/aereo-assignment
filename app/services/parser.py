"""Reads geospatial files (via GDAL/pyogrio) into GeoDataFrames, one per layer."""

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from pyproj import CRS

from app.core.errors import ProcessingError


@dataclass
class ParsedLayer:
    name: str
    crs: CRS
    frame: gpd.GeoDataFrame


def _layer_names(path: Path) -> list[str]:
    try:
        return [str(row[0]) for row in pyogrio.list_layers(path)]
    except Exception as exc:  # noqa: BLE001
        raise ProcessingError("The file could not be read as a Shapefile or KML") from exc


def parse_datasets(
    paths: list[Path],
    *,
    assume_crs: CRS | None,
    max_features: int,
    max_vertices: int,
) -> list[ParsedLayer]:
    """Read every layer of every dataset, enforcing size limits and CRS presence."""
    layers: list[ParsedLayer] = []
    feature_total = 0
    vertex_total = 0

    for path in paths:
        for name in _layer_names(path):
            try:
                declared = int(pyogrio.read_info(path, layer=name).get("features", -1))
                if declared >= 0 and feature_total + declared > max_features:
                    raise ProcessingError(f"File contains more than {max_features} features")
                frame = gpd.read_file(path, layer=name, engine="pyogrio")
            except ProcessingError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProcessingError(f"Layer '{name}' could not be read: file may be corrupt") from exc

            if frame.empty:
                continue

            feature_total += len(frame)
            if feature_total > max_features:
                raise ProcessingError(f"File contains more than {max_features} features")
            vertex_total += int(shapely.get_num_coordinates(np.asarray(frame.geometry.values)).sum())
            if vertex_total > max_vertices:
                raise ProcessingError("File contains too many vertices to process safely")

            crs = frame.crs or assume_crs
            if crs is None:
                raise ProcessingError(
                    f"Layer '{name}' has no coordinate reference system (missing .prj?). "
                    "Re-upload with the form field assume_crs=EPSG:<code>"
                )
            if not (crs.is_geographic or crs.is_projected):
                raise ProcessingError(f"Layer '{name}' uses an unsupported CRS type: {crs.name}")

            layers.append(ParsedLayer(name=name, crs=crs, frame=frame))

    if not layers:
        raise ProcessingError("The file contains no features")
    return layers
