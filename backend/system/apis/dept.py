from typing import List

from django.shortcuts import get_object_or_404
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from system.models import Dept
from utils.meet_crud import create, delete, retrieve, update
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import MeetResponse
from utils.list_to_tree import list_to_tree

router = Router()


class Filters(MeetFilters):
    name: str = Field(None, alias="name")
    status: bool = Field(None, alias="status")
    id: str = Field(None, alias="id")


class SchemaIn(ModelSchema):
    parent_id: int = None

    class Config:
        model = Dept
        model_exclude = ['id', 'parent', 'create_datetime', 'update_datetime']


class SchemaOut(ModelSchema):
    deptid: int = Field(..., alias="id")
    class Config:
        model = Dept
        model_exclude = ['id']


# @router.post("/dept", response=SchemaOut)
# def create_dept(request, data: SchemaIn):
#     dept = create(request, data, Dept)
#     return dept


# @router.delete("/dept/{dept_id}")
# def delete_dept(request, dept_id: int):
#     delete(dept_id, Dept)
#     return {"success": True}


# @router.put("/dept/{dept_id}", response=SchemaOut)
# def update_dept(request, dept_id: int, data: SchemaIn):
#     dept = update(request, dept_id, data, Dept)
#     return dept


@router.get("/dept", response=List[SchemaOut])
@paginate(MyPagination)
def list_dept(request, filters: Filters = Query(...)):
    """
    分页获取部门列表（前台用户应当可以查到所有部门）
    """
    qs = retrieve(request, Dept, filters)
    return qs


@router.get("/dept/{dept_id}", response=SchemaOut)
def get_dept(request, dept_id: int):
    """
    获取部门详情
    """
    dept = get_object_or_404(Dept, id=dept_id)
    return dept


@router.get("/dept/list/tree")
def list_dept_tree(request, filters: Filters = Query(...)):
    """
    获取部门列表树（前台用户应当可以查到所有用户）
    """
    qs = retrieve(request, Dept, filters).values()
    dept_tree = list_to_tree(list(qs))
    return MeetResponse(data=dept_tree)