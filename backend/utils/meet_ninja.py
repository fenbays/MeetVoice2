from typing import Any, List

from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from ninja import Field, ModelSchema, NinjaAPI, Query, Router, Schema
from ninja.orm.metaclass import ModelSchemaMetaclass
from ninja.pagination import PaginationBase
from ninja.types import DictStrAny

from .meet_response import MeetResponse
from .usual import get_user_info_from_token


class MeetNinjaAPI(NinjaAPI):
    def create_response(
            self, request: HttpRequest, data: Any, *, status: int = 200, code: int = 2000, msg: str = "success",
            temporal_response: HttpResponse = None,
    ) -> HttpResponse:
        return MeetResponse(errcode=code, errmsg=msg, data=data)


class MyPagination(PaginationBase):
    class Input(Schema):
        pageSize: int = Field(10, gt=0)
        page: int = Field(1, gt=-1)

    class Output(Schema):
        page: int
        limit: int
        items: List[Any]
        total: int

    def paginate_queryset(
            self,
            queryset: QuerySet,
            pagination: Input,
            **params: DictStrAny,
    ) -> Any:
        offset = pagination.pageSize * (pagination.page - 1)
        limit: int = pagination.pageSize
        return {
            "page": offset,
            "limit": limit,
            "items": queryset[offset: offset + limit],
            "total": self._items_count(queryset),
        }  # noqa: E203


class MeetFilters(Schema):
    creator_id: int = Field(None, alias="creator_id")
    belong_dept: int = Field(None, alias="belong_dept")
    belong_dept__in: List[int] = Field(None, alias="belong_dept__in")