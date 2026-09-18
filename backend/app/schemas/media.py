from __future__ import annotations

from pydantic import BaseModel

from app.models.media_asset import MediaAsset, MediaType


class MediaFormatImage(BaseModel):
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None


class MediaFormatVideo(BaseModel):
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    mime_type: str | None = None


class MediaFormatModel3D(BaseModel):
    format: str | None = None
    file_size_bytes: int | None = None


class MediaFormatSequence360(BaseModel):
    frame_count: int | None = None
    width: int | None = None
    height: int | None = None


MediaFormat = MediaFormatImage | MediaFormatVideo | MediaFormatModel3D | MediaFormatSequence360


class MediaAssetPublic(BaseModel):
    type: str
    role: str
    url: str
    alt_text: str | None
    position: int
    format: MediaFormat


def _build_format(media_type: MediaType, format_metadata: dict | None) -> MediaFormat:
    meta = format_metadata or {}
    if media_type == MediaType.image:
        return MediaFormatImage(
            width=meta.get("width"),
            height=meta.get("height"),
            mime_type=meta.get("mime_type"),
        )
    if media_type == MediaType.video:
        return MediaFormatVideo(
            width=meta.get("width"),
            height=meta.get("height"),
            duration_seconds=meta.get("duration_seconds"),
            mime_type=meta.get("mime_type"),
        )
    if media_type == MediaType.model_3d:
        return MediaFormatModel3D(
            format=meta.get("format"),
            file_size_bytes=meta.get("file_size_bytes"),
        )
    if media_type == MediaType.sequence_360:
        return MediaFormatSequence360(
            frame_count=meta.get("frame_count"),
            width=meta.get("width"),
            height=meta.get("height"),
        )
    raise ValueError(f"Unknown media_type: {media_type!r}")


def media_asset_to_public(asset: MediaAsset) -> MediaAssetPublic:
    """Map an internal MediaAsset row to its public representation
    (API_CONTRACT.md section 6). storage_path is never exposed; url is
    derived from it with the smallest deterministic mapping approved for
    Sprint 3 (no real storage/CDN infrastructure exists yet)."""
    return MediaAssetPublic(
        type=asset.media_type.value,
        role=asset.role.value,
        url=f"/media/{asset.storage_path}",
        alt_text=asset.alt_text,
        position=asset.position,
        format=_build_format(asset.media_type, asset.format_metadata),
    )
