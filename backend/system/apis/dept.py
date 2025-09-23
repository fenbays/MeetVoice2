from typing import List

from django.shortcuts import get_object_or_404
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from utils.meet_auth import data_permission
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

class DeptFilters(MeetFilters):
    deptid: int = Field(None, alias="deptid")

@router.get("/dept/list", response=List[SchemaOut])
@paginate(MyPagination)
def list_dept(request, filters: DeptFilters = Query(...)):
    """
    分页获取部门列表（前台用户应当可以查到所有部门）
    """
    filters = data_permission(request, filters)
    if filters.deptid is not None:
        qs = Dept.objects.filter(id=filters.deptid)
    else:
        qs = Dept.objects.all()
    qs = qs.order_by('create_datetime')
    return qs


@router.get("/dept/get", response=SchemaOut)
def get_dept(request, dept_id: int = Query(...)):
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