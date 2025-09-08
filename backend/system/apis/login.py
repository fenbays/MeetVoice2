
from datetime import datetime

from django.contrib import auth
from django.forms import model_to_dict
from django.shortcuts import get_object_or_404
from ninja import Router, ModelSchema, Query, Schema, Field
from django.core.cache import cache
from meetvoice.settings import SECRET_KEY, TOKEN_LIFETIME
from system.models import Users
from utils.meet_jwt import MeetJwt
from utils.meet_response import MeetResponse, BusinessCode
from utils.request import save_login_log
from utils.usual import get_user_info_from_token
from utils.meet_token import TokenManager

router = Router()


class SchemaOut(ModelSchema):
    userid: int = Field(..., alias="id")

    class Config:
        model = Users
        model_exclude = ['id', 'password', 'belong_dept']


class LoginSchema(Schema):
    username: str = Field(None, alias="username")
    password: str = Field(None, alias="password")


class Out(Schema):
    userInfo: SchemaOut
    token: str


@router.post("/login", response=Out, auth=None)
def login(request, data: LoginSchema):
    user_obj = auth.authenticate(request, **data.dict())
    if not user_obj:
        return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg="账号/密码错误")

    request.user = user_obj
    role = user_obj.role.all().values('id')
    role_list = []
    for item in role:
        role_list.append(item['id'])
    user_obj_dic = model_to_dict(user_obj)
    user_obj_dic['role'] = role_list
    del user_obj_dic['password']
    del user_obj_dic['avatar']
    user_obj_dic['dept'] = user_obj.dept.id if user_obj.dept else None
    time_now = int(datetime.now().timestamp())
    jwt = MeetJwt(SECRET_KEY, user_obj_dic, valid_to=time_now + TOKEN_LIFETIME)
    token = jwt.encode()
    device_id = request.headers.get('X-Device-ID', 'web')
    token_manager = TokenManager()
    token_manager.store_token(user_obj.id, token, device_id)
    data = {
        # "sysAllDictItems": "q",
        # "departs": "e",
        'userInfo': user_obj,
        'token': token
    }
    save_login_log(request=request)
    return data


@router.get("/logout", auth=None)
def get_post(request):
    auth_header = request.META.get("HTTP_AUTHORIZATION")
    token = auth_header.split(" ")[1] if auth_header and auth_header.startswith("Bearer ") else None
    if not token:
        return MeetResponse(errcode=BusinessCode.INVALID_TOKEN, errmsg="无效token")
    user_info = get_user_info_from_token(request)
    token_manager = TokenManager()
    if not token_manager.is_valid(user_info['id'], token):
        return MeetResponse(errcode=BusinessCode.INVALID_TOKEN, errmsg="无效token")

    token_manager.revoke_token(user_info['id'], token)
    return MeetResponse(errmsg="注销成功")


@router.get("/userinfo", response=SchemaOut)
def get_userinfo(request):
    user_info = get_user_info_from_token(request)
    user = get_object_or_404(Users, id=user_info['id'])
    return user