"""Pydantic response models (these also drive the Swagger documentation)."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["3f6c0a1e-8d2b-4c55-9a43-6f1d2b7c9e10"])
    filename: str = Field(examples=["survey.kml"])
    file_type: str = Field(examples=["kml"], description="'shapefile' or 'kml'")
    size_bytes: int
    status: str = Field(examples=["COMPLETED"], description="PROCESSING | COMPLETED | FAILED")
    crs: str | None = Field(default=None, examples=["EPSG:4326"], description="'MIXED' if layers differ")
    feature_count: int
    geometry_summary: dict[str, int] | None = Field(
        default=None, examples=[{"Polygon": 10, "LineString": 4, "Point": 2}]
    )
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # SQLite returns naive datetimes; everything we store is UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class MeasurementOut(BaseModel):
    feature_index: int
    layer: str | None = None
    geometry_type: str | None = None
    status: str = Field(description="MEASURED | NOT_APPLICABLE | UNSUPPORTED")
    area_sq_m: float | None = Field(default=None, description="Area in m² measured in the UTM projection")
    area_hectares: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = Field(
        default=None, description="Length in metres measured in the UTM projection"
    )
    geodesic_area_sq_m: float | None = Field(default=None, description="Ellipsoidal (WGS84) cross-check")
    geodesic_length_m: float | None = Field(default=None, description="Ellipsoidal (WGS84) cross-check")
    projected_crs: str | None = Field(default=None, examples=["EPSG:32644"])
    note: str | None = None


class MeasurementSummary(BaseModel):
    total_features: int
    measured: int
    not_applicable: int
    unsupported: int
    total_area_sq_m: float
    total_length_m: float


class Pagination(BaseModel):
    limit: int
    offset: int
    total: int


class MeasurementsResponse(BaseModel):
    file_id: str
    summary: MeasurementSummary
    pagination: Pagination
    measurements: list[MeasurementOut]


class FeatureOut(BaseModel):
    index: int
    layer: str | None = None
    geometry_type: str | None = None
    crs: str | None = None
    geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON geometry in the source CRS")
    properties: dict[str, Any]
    measurement: MeasurementOut


class FeaturesResponse(BaseModel):
    file_id: str
    pagination: Pagination
    features: list[FeatureOut]
