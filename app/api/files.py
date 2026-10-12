"""File upload, inspection and measurement endpoints."""

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.core.errors import InvalidUploadError
from app.core.security import require_api_key
from app.models import FeatureRecord, FileStatus, MeasureStatus, UploadedFile
from app.schemas import (
    FeatureOut,
    FeaturesResponse,
    FileOut,
    MeasurementOut,
    MeasurementsResponse,
    MeasurementSummary,
    Pagination,
)
from app.services.crs import parse_assume_crs
from app.services.kml import validate_kml
from app.services.processor import process_file
from app.services.safe_zip import extract_shapefile_zip
from app.services.uploads import detect_file_type, sanitize_filename, save_upload

router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(require_api_key)])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Limit = Annotated[int, Query(ge=1, le=1000, description="Page size")]
Offset = Annotated[int, Query(ge=0, description="Number of items to skip")]


def _get_file_or_404(db: Session, file_id: uuid.UUID) -> UploadedFile:
    record = db.get(UploadedFile, str(file_id))
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status != FileStatus.COMPLETED.value:
        detail = f"File status is {record.status}"
        if record.error_message:
            detail += f": {record.error_message}"
        raise HTTPException(status_code=409, detail=detail)


def _measurement_out(row: FeatureRecord) -> MeasurementOut:
    return MeasurementOut(
        feature_index=row.index,
        layer=row.layer,
        geometry_type=row.geometry_type,
        status=row.measure_status,
        area_sq_m=row.area_sq_m,
        area_hectares=None if row.area_sq_m is None else round(row.area_sq_m / 10_000, 6),
        perimeter_m=row.perimeter_m,
        length_m=row.length_m,
        geodesic_area_sq_m=row.geodesic_area_sq_m,
        geodesic_length_m=row.geodesic_length_m,
        projected_crs=row.projected_crs,
        note=row.measure_note,
    )


@router.post(
    "/",
    response_model=FileOut,
    status_code=201,
    summary="Upload a Shapefile (.zip) or KML and process it",
)
def upload_file(
    db: DbSession,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="A .zip containing a Shapefile, or a .kml file")],
    assume_crs: Annotated[
        str | None,
        Form(description="Only used if the file has no CRS (e.g. Shapefile without .prj). Format: EPSG:4326"),
    ] = None,
):
    """Validate, parse and measure an uploaded file.

    * Invalid or unsafe uploads are rejected with a 4xx error and nothing is stored.
    * A valid upload that cannot be processed (e.g. no CRS) is stored with `status=FAILED`
      and an `error_message`, so it can be inspected via `GET /api/files/{id}/`.
    """
    filename = sanitize_filename(file.filename)
    file_type = detect_file_type(filename)

    crs_hint = None
    if assume_crs:
        try:
            crs_hint = parse_assume_crs(assume_crs)
        except ValueError as exc:
            raise InvalidUploadError(str(exc)) from exc

    # Untrusted bytes only ever live in a throw-away temp dir, under names *we* choose.
    with tempfile.TemporaryDirectory(prefix="geoapi_") as tmp:
        workdir = Path(tmp)
        stored = workdir / ("upload.zip" if file_type == "shapefile" else "upload.kml")
        size = save_upload(file.file, stored, settings.max_upload_bytes)

        if file_type == "shapefile":
            datasets = extract_shapefile_zip(
                stored,
                workdir / "extracted",
                max_entries=settings.max_zip_entries,
                max_total_bytes=settings.max_unzipped_bytes,
                max_ratio=settings.max_compression_ratio,
            )
        else:
            validate_kml(stored)
            datasets = [stored]

        record = UploadedFile(
            id=str(uuid.uuid4()),
            filename=filename,
            file_type=file_type,
            size_bytes=size,
            status=FileStatus.PROCESSING.value,
        )
        db.add(record)
        db.commit()

        process_file(db, record, datasets, crs_hint, settings)

    db.refresh(record)
    return record


@router.get("/", response_model=list[FileOut], summary="List uploaded files (newest first)")
def list_files(db: DbSession, limit: Limit = 50, offset: Offset = 0):
    stmt = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@router.get("/{file_id}/", response_model=FileOut, summary="File information")
def get_file(file_id: uuid.UUID, db: DbSession):
    return _get_file_or_404(db, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Area / length measurements for every feature",
)
def get_measurements(file_id: uuid.UUID, db: DbSession, limit: Limit = 100, offset: Offset = 0):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    totals = db.execute(
        select(
            func.count(FeatureRecord.id),
            func.coalesce(
                func.sum(case((FeatureRecord.measure_status == MeasureStatus.MEASURED.value, 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case((FeatureRecord.measure_status == MeasureStatus.NOT_APPLICABLE.value, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((FeatureRecord.measure_status == MeasureStatus.UNSUPPORTED.value, 1), else_=0)),
                0,
            ),
            func.coalesce(func.sum(FeatureRecord.area_sq_m), 0.0),
            func.coalesce(func.sum(FeatureRecord.length_m), 0.0),
        ).where(FeatureRecord.file_id == record.id)
    ).one()

    rows = db.scalars(
        select(FeatureRecord)
        .where(FeatureRecord.file_id == record.id)
        .order_by(FeatureRecord.index)
        .limit(limit)
        .offset(offset)
    ).all()

    return MeasurementsResponse(
        file_id=record.id,
        summary=MeasurementSummary(
            total_features=totals[0],
            measured=totals[1],
            not_applicable=totals[2],
            unsupported=totals[3],
            total_area_sq_m=round(totals[4], 4),
            total_length_m=round(totals[5], 4),
        ),
        pagination=Pagination(limit=limit, offset=offset, total=totals[0]),
        measurements=[_measurement_out(r) for r in rows],
    )


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesResponse,
    summary="Full features: geometry, CRS, properties and measurements",
)
def get_features(
    file_id: uuid.UUID,
    db: DbSession,
    limit: Limit = 100,
    offset: Offset = 0,
    include_geometry: bool = True,
):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    rows = db.scalars(
        select(FeatureRecord)
        .where(FeatureRecord.file_id == record.id)
        .order_by(FeatureRecord.index)
        .limit(limit)
        .offset(offset)
    ).all()

    return FeaturesResponse(
        file_id=record.id,
        pagination=Pagination(limit=limit, offset=offset, total=record.feature_count),
        features=[
            FeatureOut(
                index=r.index,
                layer=r.layer,
                geometry_type=r.geometry_type,
                crs=r.crs,
                geometry=r.geometry if include_geometry else None,
                properties=r.properties,
                measurement=_measurement_out(r),
            )
            for r in rows
        ],
    )


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and all its features")
def delete_file(file_id: uuid.UUID, db: DbSession) -> Response:
    record = _get_file_or_404(db, file_id)
    db.delete(record)
    db.commit()
    return Response(status_code=204)
