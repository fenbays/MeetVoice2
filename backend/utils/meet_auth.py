import re
from datetime import datetime

from django.core.cache import cache
from utils.meet_token import TokenManager
from meetvoice.settings import DEBUG, SECRET_KEY, WHITE_LIST
from ninja.security import HttpBearer
from system.models import MenuButton, Users

from .meet_jwt import MeetJwt
from .meet_ninja import MeetFilters
from .usual import get_dept, get_user_info_from_token
from .meet_response import BusinessCode, MeetError

METHOD = {
    'GET': 0,
    'POST': 1,
    'PUT': 2,
    'DELETE': 3,
}

class GlobalAuth(HttpBearer):
    def authenticate(self, request, token):
        jwt = MeetJwt(SECRET_KEY)

        # 第1步：JWT格式和时间验证
        try:
            value = jwt.decode(SECRET_KEY, token)
            time_now = int(datetime.now().timestamp())
            if value.valid_to < time_now:
                raise MeetError("凭证过期", BusinessCode.INVALID_TOKEN.value)
        except Exception:
            raise MeetError("凭证无效", BusinessCode.INVALID_TOKEN.value)
        
        # 第2步：验证token未被撤销
        token_user = value.payload
        token_manager = TokenManager()
        if not token_manager.is_valid(token_user['id'], token):
            raise MeetError("凭证无效", BusinessCode.INVALID_TOKEN.value)
        
        # 第3步：验证用户当前状态（关键！）
        try:
            user = Users.objects.get(id=token_user['id'])
            if not user.is_active: 
                raise MeetError("用户已被禁用", BusinessCode.PERMISSION_DENIED.value)
            if user.user_type != 1:
                raise MeetError("不是前台用户", BusinessCode.PERMISSION_DENIED.value)
        except Users.DoesNotExist:
            # 用户被删除，立即清理相关token
            token_manager.revoke_user_all_tokens(token_user['id'])
            raise MeetError("用户不存在", BusinessCode.USER_NOT_FOUND.value)

        return token


def data_permission(request, filters: MeetFilters):
    user_info = get_user_info_from_token(request)
    if user_info['is_superuser']:
        return filters
    user = Users.objects.get(id=user_info['id'])
    data_range_qs = user.role.values_list('data_range', flat=True)
    dept_ids = user.role.values_list('dept__id', flat=True)

    # 如果有多个角色，取数据权限最大的角色
    data_range = max(list(data_range_qs))

    # 仅本人数据权限
    if data_range == 0:
        filters.creator_id = user_info['id']

    # 本部门数据权限
    if data_range == 1:
        filters.belong_dept = user_info['dept']

    # 本部门及以下数据权限
    if data_range == 2:
        dept_and_below_ids = get_dept(user_info['dept'])
        filters.belong_dept__in = dept_and_below_ids

    # 自定义数据权限
    if data_range == 3:
        pass

    # 所有数据权限
    if data_range == 4:
        filters.belong_dept__in = list(dept_ids)

    return filters