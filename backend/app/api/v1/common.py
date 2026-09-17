from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import String, cast

from app.models.media_asset import MediaAsset

# API_CONTRACT.md section 10: unknown slug, unpublished record, and a record
# made ineligible by a related entity's publication state (e.g. a published
# piece under an unpublished artisan) must be byte-for-byte identical
# externally, so there is exactly one 404 body shared by every public
# detail endpoint.
NOT_FOUND_ERROR = {"code": "not_found", "message": "The requested resource does not exist."}


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND_ERROR)


def media_order_by():
    # Deterministic tie-break shared by every public endpoint that lists
    # media: position ASC, role ASC (as text, not native enum OID order),
    # storage_path ASC.
    return (MediaAsset.position.asc(), cast(MediaAsset.role, String).asc(), MediaAsset.storage_path.asc())
