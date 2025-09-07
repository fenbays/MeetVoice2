from typing import List, Optional

from django.contrib.auth.hashers import make_password
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from system.models import Users
from system.validators import validate_password_complexity
from utils.meet_crud import create, delete, retrieve
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import BusinessCode, MeetResponse
from utils.usual import get_user_info_from_token
from utils.meet_token import TokenManager
import logging

router = Router()

logger = logging.getLogger(__name__)


class Filters(MeetFilters):
    name: str = Field(None, alias="name")
    mobile: str = Field(None, alias="mobile")
    status: bool = Field(None, alias="status")
    dept_id__in: list = Field(None, alias="dept_ids[]")
    id: int = Field(None, alias="id")


class SchemaIn(ModelSchema):
    dept_id: Optional[int] = Field(None, alias="dept")
    email: Optional[str] = Field(None, alias="email")
    mobile: Optional[str] = Field(None, alias="mobile")
    name: Optional[str] = Field(None, alias="name")
    avatar: Optional[str] = Field(None, alias="avatar")
    gender: Optional[int] = Field(None, alias="gender")
    first_name: Optional[str] = Field(None, alias="first_name")
    last_name: Optional[str] = Field(None, alias="last_name")
    home_path: Optional[str] = Field(None, alias="home_path")

    class Config:
        model = Users
        model_fields = ['id', 'email', 'mobile', 'name', 'avatar', 'gender', 'dept', 'first_name', 'last_name', 'home_path']


class SchemaOut(ModelSchema):
    class Config:
        model = Users
        model_exclude = ['password', 'creator', 'modifier', 'groups', 'user_permissions', 'role']

class SimpleUserSchemaOut(ModelSchema):
    userid: int = Field(..., alias="id")
    class Config:
        model = Users
        model_fields = ['username', 'name', 'email', 'mobile', 'avatar']

@router.get("/user/simplelist", response=List[SimpleUserSchemaOut])
@paginate(MyPagination)
def all_list_user(request):
    '''
    获取用户列表
    '''
    users = Users.objects.filter(is_active=True, is_superuser=False)
    return list(users)

@router.post("/user/update", response=SchemaOut)
def update_user(request, payload: SchemaIn):
    """
    更新用户信息
    """
    # 获取当前登录用户信息
    current_user_info = get_user_info_from_token(request)
    current_user_id = current_user_info['id']
    
    # 检查是否在更新自己的信息
    if not hasattr(payload, 'id') or payload.id != current_user_id:
        logger.warning(f'只能更新自己的信息1: {payload.id} != {current_user_id}')
        return MeetResponse(
            errcode=BusinessCode.PERMISSION_DENIED, 
            errmsg='只能更新自己的信息'
        )
    
    # 获取要更新的用户对象
    user = get_object_or_404(Users, id=payload.id, is_active=True, is_superuser=False)
    
    # 确保只能更新自己的信息
    if user.id != current_user_id:
        logger.warning(f'只能更新自己的信息2: {user.id} != {current_user_id}')
        return MeetResponse(
            errcode=BusinessCode.PERMISSION_DENIED, 
            errmsg='只能更新自己的信息'
        )
    
    # 更新用户信息
    for attr, value in payload.dict().items():
        if hasattr(user, attr) and value is not None:
            setattr(user, attr, value)
    
    logger.info(f'更新用户信息: {user.id} {payload.dict()}')
    user.save()
    return user

@router.get("/user/department/simplelist", response=List[SimpleUserSchemaOut])
def get_department_member(request, department_id: int = Query(...)):
    """
    获取指定部门下的所有活跃成员列表
    
    Args:
        request: Django请求对象
        dept_id (int): 部门ID（通过查询参数传递，如 ?dept_id=1）
        
    Returns:
        List[SchemaOut]: 部门成员列表

    Notes:
        前台用户可以查到其他部门的成员（仅包含is_active=True的成员简单信息）
    """
    users = Users.objects.filter(dept_id=department_id, is_active=True, is_superuser = False)
    return list(users)

@router.get("/user/get", response=SimpleUserSchemaOut)
def get_user(request, userid: int = Query(...)):
    """
    获取指定用户的信息（前台用户）
    """
    try:
        user = Users.objects.get(id=userid, user_type=1, is_active=True, is_superuser=False)
    except Users.DoesNotExist:
        return MeetResponse(
            errcode=BusinessCode.USER_NOT_FOUND,
            errmsg="用户不存在或不是前台用户"
        )
    return user


class PasswordSchemaIn(Schema):
    id: int
    new_password: str
    old_password: str

@router.post("/user/repassword", response=SchemaOut)
def repassword(request, data: PasswordSchemaIn):
    """
    用户修改密码
    """
    try:
        if data.old_password == data.new_password:
            return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg='新密码不能与原密码相同')
        validate_password_complexity(data.new_password)
    except ValidationError as e:
        return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg=str(e))

    request_user = get_user_info_from_token(request)
    request_user_id = request_user['id']
    update_id = data.id
    if request_user_id == update_id:
        user = get_object_or_404(Users, id=update_id, is_active=True, is_superuser=False)
        if not user.check_password(data.old_password) or data.old_password == data.new_password:
            return MeetResponse(errcode=BusinessCode.PERMISSION_DENIED, errmsg='原密码错误')        
        user.set_password(data.new_password)
        user.save()
        token_manager = TokenManager()
        token_manager.revoke_user_all_tokens(update_id)
        return MeetResponse(errmsg='密码修改成功')
    else:
        return MeetResponse(errcode=BusinessCode.PERMISSION_DENIED, errmsg='只能修改自己的密码')