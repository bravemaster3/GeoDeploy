from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class SetupConfig(Base):
    __tablename__ = "setup_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    postgis_type: Mapped[str | None] = mapped_column(String(16))   # local | external
    postgis_host: Mapped[str | None] = mapped_column(String(256))
    postgis_port: Mapped[int | None] = mapped_column(Integer, default=5432)
    postgis_db: Mapped[str | None] = mapped_column(String(128))
    postgis_user: Mapped[str | None] = mapped_column(String(128))
    postgis_password: Mapped[str | None] = mapped_column(Text)  # encrypted at rest

    storage_type: Mapped[str | None] = mapped_column(String(16))   # local | s3 | hetzner | r2 | backblaze
    storage_endpoint: Mapped[str | None] = mapped_column(String(512))
    storage_bucket: Mapped[str | None] = mapped_column(String(256))
    storage_access_key: Mapped[str | None] = mapped_column(String(256))
    storage_secret_key: Mapped[str | None] = mapped_column(Text)   # encrypted at rest
    storage_region: Mapped[str | None] = mapped_column(String(64), default="us-east-1")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    vector_layers: Mapped[list["VectorLayer"]] = relationship(back_populates="user")
    raster_layers: Mapped[list["RasterLayer"]] = relationship(back_populates="user")
    portals: Mapped[list["Portal"]] = relationship(back_populates="user")


class VectorLayer(Base):
    __tablename__ = "vector_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    crs: Mapped[str | None] = mapped_column(String(64))
    feature_count: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[str | None] = mapped_column(Text)      # JSON [minx, miny, maxx, maxy]
    columns: Mapped[str | None] = mapped_column(Text)   # JSON [{name, type}]
    geometry_type: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)
    storage_backend: Mapped[str] = mapped_column(String(16), default="postgis")  # postgis | geoparquet
    s3_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | ready | error
    error_message: Mapped[str | None] = mapped_column(Text)
    default_style: Mapped[str | None] = mapped_column(Text)  # JSON {opacity, style, popup_fields}
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="vector_layers")


class RasterLayer(Base):
    __tablename__ = "raster_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    crs: Mapped[str | None] = mapped_column(String(64))
    bbox: Mapped[str | None] = mapped_column(Text)
    band_count: Mapped[int | None] = mapped_column(Integer)
    nodata_value: Mapped[float | None] = mapped_column(Float)
    file_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text)
    default_style: Mapped[str | None] = mapped_column(Text)  # JSON {opacity}
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="raster_layers")


class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    layer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    layer_type: Mapped[str] = mapped_column(String(8), nullable=False)  # vector | raster
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued | processing | ready | error
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Portal(Base):
    __tablename__ = "portals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[str] = mapped_column(String(128), default="minimal")
    layer_configs: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    initial_view: Mapped[str | None] = mapped_column(Text)  # JSON {center:[lng,lat], zoom, bearing, pitch} — published portal's start view
    access_type: Mapped[str] = mapped_column(String(16), default="public")  # public | password | private
    access_password_hash: Mapped[str | None] = mapped_column(String(256))    # bcrypt — for future server-side auth
    access_password_sha256: Mapped[str | None] = mapped_column(String(64))   # SHA-256 hex — embedded in published portal
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="portals")
