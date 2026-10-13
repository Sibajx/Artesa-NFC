from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ListMeta(BaseModel):
    total: int


class ListEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: ListMeta


class PagedListMeta(ListMeta):
    """PEND-029: ``total`` counts the whole filtered set; ``next_cursor`` is
    the opaque cursor of the next page, null on the last one (and always null
    when the request asked for no ``limit``)."""

    next_cursor: str | None = None


class PagedListEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: PagedListMeta


class ErrorDetail(BaseModel):
    field: str
    reason: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
