"""Orchestrates: parse -> measure -> persist, and maintains the file status."""

import json
import logging
import math
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import CRS
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import ProcessingError
from app.models import FeatureRecord, FileStatus, UploadedFile, utcnow
from app.services.crs import crs_label
from app.services.measurements import GeometryMeasurer
from app.services.parser import parse_datasets

logger = logging.getLogger("geo_api.processor")
_BATCH = 1000


def to_jsonable(value):
    """Turn pandas/numpy scalars into plain JSON-safe Python values."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def process_file(
    db: Session,
    record: UploadedFile,
    dataset_paths: list[Path],
    assume_crs: CRS | None,
    settings: Settings,
) -> None:
    """Fill in `record` and its features. Never raises: failures become status=FAILED."""
    try:
        layers = parse_datasets(
            dataset_paths,
            assume_crs=assume_crs,
            max_features=settings.max_features,
            max_vertices=settings.max_vertices,
        )

        index = 0
        geometry_types: Counter[str] = Counter()
        crs_labels: set[str] = set()
        batch: list[dict] = []

        for layer in layers:
            measurer = GeometryMeasurer(layer.crs)
            label = crs_label(layer.crs)
            crs_labels.add(label or "UNKNOWN")
            geom_column = layer.frame.geometry.name
            properties = layer.frame.drop(columns=[geom_column]).to_dict("records")

            for geom, props in zip(layer.frame.geometry, properties, strict=True):
                result = measurer.measure(geom)
                geometry_types[geom.geom_type if geom is not None else "None"] += 1
                batch.append(
                    {
                        "file_id": record.id,
                        "index": index,
                        "layer": layer.name,
                        "geometry_type": geom.geom_type if geom is not None else None,
                        "geometry": (
                            json.loads(shapely.to_geojson(geom))
                            if geom is not None and not geom.is_empty
                            else None
                        ),
                        "crs": label,
                        "properties": {str(k): to_jsonable(v) for k, v in props.items()},
                        "measure_status": result.status.value,
                        "area_sq_m": result.area_sq_m,
                        "perimeter_m": result.perimeter_m,
                        "length_m": result.length_m,
                        "geodesic_area_sq_m": result.geodesic_area_sq_m,
                        "geodesic_length_m": result.geodesic_length_m,
                        "projected_crs": result.projected_crs,
                        "measure_note": result.note,
                    }
                )
                index += 1
                if len(batch) >= _BATCH:
                    db.bulk_insert_mappings(FeatureRecord, batch)
                    batch.clear()

        if batch:
            db.bulk_insert_mappings(FeatureRecord, batch)

        record.feature_count = index
        record.crs = crs_labels.pop() if len(crs_labels) == 1 else "MIXED"
        record.geometry_summary = dict(geometry_types)
        record.status = FileStatus.COMPLETED.value
        record.error_message = None

    except ProcessingError as exc:
        db.rollback()
        _mark_failed(record, exc.message)
    except Exception:  # noqa: BLE001 - never leak internals to the client
        db.rollback()
        logger.exception("Unexpected error while processing file %s", record.id)
        _mark_failed(record, "Unexpected error while processing the file")

    record.completed_at = utcnow()
    db.add(record)
    db.commit()


def _mark_failed(record: UploadedFile, message: str) -> None:
    record.status = FileStatus.FAILED.value
    record.error_message = message
    record.feature_count = 0
