import enum

from sqlalchemy import Enum as SAEnum


class PublicationStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


# Shared across artisan.publication_status and piece.publication_status
# (DATA_MODEL.md section 9): a single Enum instance ensures the native
# Postgres type "publication_status" is created once and reused, rather
# than emitted twice by two separate column type objects of the same name.
publication_status_enum = SAEnum(PublicationStatus, name="publication_status")
