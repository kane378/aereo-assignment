"""Database models.

UploadedFile  1 --- *  FeatureRecord

Geometries are stored as GeoJSON (in the file's *original* CRS) and the
measurement results are stored next to each feature, so reading them back never
needs to re-run any geospatial computation.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class FileStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasureStatus(enum.StrEnum):
    MEASURED = "MEASURED"  # area and/or length computed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. Point: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # empty / unknown geometry / failed transform


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # "shapefile" | "kml"
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=FileStatus.PROCESSING.value)
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    geometry_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    features: Mapped[list["FeatureRecord"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )


class FeatureRecord(Base):
    __tablename__ = "features"
    __table_args__ = (Index("ix_features_file_index", "file_id", "index", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer)  # 0-based position across the whole file
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geometry_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, source CRS
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    measure_status: Mapped[str] = mapped_column(String(16))
    area_sq_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    geodesic_area_sq_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_crs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    measure_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
