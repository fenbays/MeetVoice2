
from datetime import datetime

from django.contrib import auth
from django.db import IntegrityError
from django.forms import model_to_dict
from django.shortcuts import get_object_or_404
from ninja import Router, ModelSchema, Query, Schema, Field
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from meetvoice.settings import SECRET_KEY, TOKEN_LIFETIME
from utils.meet_jwt import MeetJwt
from utils.meet_response import MeetResponse, BusinessCode
from utils.request import save_login_log
from utils.usual import get_user_info_from_token
from utils.meet_token import TokenManager
User = get_user_model()
router = Router()


class SchemaOut(ModelSchema):
    userid: int = Field(..., alias="id")

    class Config:
        model = User
        model_exclude = ['id', 'password', 'belong_dept']


class LoginSchema(Schema):
    username: str = Field(..., alias="username")
    password: str = Field(..., alias="password")


class Out(Schema):
    userInfo: SchemaOut
    token: str
    expires_in: int


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
        'userInfo': user_obj,
        'token': token,
        'expires_in': TOKEN_LIFETIME
    }
    save_login_log(request=request)
    return data


@router.post("/logout", auth=None)
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
    user = get_object_or_404(User, id=user_info['id'])
    return user

class CheckTokenSchema(Schema):
    email: str = Field(..., alias="email")
    token: str = Field(..., alias="token")

class ActivateUserSchema(Schema):
    email: str = Field(..., alias="email") 
    token: str = Field(..., alias="token")
    password: str = Field(..., alias="password")

@router.post("/activate/check", auth=None)
def check_invite_token(request, data: CheckTokenSchema):
    """验证邀请token是否有效"""
    token_manager = TokenManager()
    
    if not token_manager.is_invite_token_valid(data.email, data.token):
        return MeetResponse(errcode=BusinessCode.INVALID_TOKEN, errmsg="邀请链接无效或已过期")
    
    # 检查用户是否存在且未激活
    try:
        user = User.objects.get(email=data.email)
        if user.is_active:
            return MeetResponse(errcode=BusinessCode.BUSINESS_ERROR, errmsg="用户已激活")
    except User.DoesNotExist:
        return MeetResponse(errcode=BusinessCode.INSTANCE_NOT_FOUND, errmsg="用户不存在")
    
    return MeetResponse(errmsg="邀请链接有效")

@router.post("/activate/set", auth=None)
def activate_user(request, data: ActivateUserSchema):
    """基于token重置密码并激活用户"""
    token_manager = TokenManager()
    
    if not token_manager.is_invite_token_valid(data.email, data.token):
        return MeetResponse(errcode=BusinessCode.INVALID_TOKEN, errmsg="邀请链接无效或已过期")
    
    try:
        user = User.objects.get(email=data.email)
        if user.is_active:
            return MeetResponse(errcode=BusinessCode.BUSINESS_ERROR, errmsg="用户已激活")
        
        # 使用Django密码验证器
        try:
            validate_password(data.password, user)
        except ValidationError as e:
            error_messages = []
            for error in e.error_list:
                error_messages.append(error.message)
            return MeetResponse(
                errcode=BusinessCode.BUSINESS_ERROR, 
                errmsg="密码不符合要求：" + "；".join(error_messages)
            )

        # 重置密码并激活用户
        user.set_password(data.password)
        user.is_active = True
        user.save()
        
        # 撤销邀请token（一次性使用）
        token_manager.revoke_invite_token(data.email)
        
        return MeetResponse(errmsg="账号激活成功，现在可以登录了")
        
    except User.DoesNotExist:
        return MeetResponse(errcode=BusinessCode.INSTANCE_NOT_FOUND, errmsg="用户不存在")
    except Exception as e:
        return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg=f"激活失败：{str(e)}")