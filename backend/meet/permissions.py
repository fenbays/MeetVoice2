"""
会议权限装饰器
Linus原则：简单、直接、无废话
"""
import json
import inspect
from functools import wraps
from django.shortcuts import get_object_or_404
from meet.models import Meeting
from system.models import Users
from utils.usual import get_user_info_from_token
from utils.meet_response import MeetError, BusinessCode


def _extract_meeting_id(request, *args, **kwargs):
    """智能提取meeting_id，支持URL参数和请求体"""
    meeting_id = None
    
    # 1. 首先尝试从URL路径参数获取
    if 'meetingid' in kwargs:
        meeting_id = kwargs['meetingid']
    elif len(args) > 0:
        # 检查第一个位置参数是否为meeting_id（兼容旧接口）
        meeting_id = args[0]
    
    # 2. 如果URL中没有，尝试从请求体获取
    if meeting_id is None:
        try:
            if hasattr(request, 'body') and request.body:
                body = json.loads(request.body)
                meeting_id = body.get('meetingid')
        except (json.JSONDecodeError, AttributeError):
            pass
    
    # 3. 最后尝试从查询参数获取（用于GET请求）
    if meeting_id is None and hasattr(request, 'GET'):
        meeting_id = request.GET.get('meetingid')
    
    if meeting_id is None:
        raise MeetError("缺少meetingid参数", BusinessCode.BUSINESS_ERROR)
    
    try:
        return int(meeting_id)
    except (ValueError, TypeError):
        raise MeetError("meetingid必须为数字", BusinessCode.BUSINESS_ERROR)


def require_meeting_edit_permission(view_func):
    """要求会议编辑权限的装饰器（智能获取meeting_id）"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        meeting_id = _extract_meeting_id(request, *args, **kwargs)
        meeting = get_object_or_404(Meeting, id=meeting_id)
        request_user = get_user_info_from_token(request)
        user_obj = Users.objects.get(id=request_user.get('id'))
        print(f"meeting.owner: {meeting.owner}, user_obj: {user_obj}")
        if not meeting.user_can_edit(user_obj):
            raise MeetError("无权限编辑此会议", BusinessCode.PERMISSION_DENIED)
        
        # 如果原函数期望meeting_id参数，确保传递
        sig = inspect.signature(view_func)
        if 'meetingid' in sig.parameters:
            kwargs['meetingid'] = meeting_id
            
        return view_func(request, *args, **kwargs)
    return wrapper


def require_meeting_view_permission(view_func):
    """要求会议查看权限的装饰器（智能获取meeting_id）"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        meeting_id = _extract_meeting_id(request, *args, **kwargs)
        meeting = get_object_or_404(Meeting, id=meeting_id)
        request_user = get_user_info_from_token(request)
        user_obj = Users.objects.get(id=request_user.get('id'))
        print(f"meeting.owner: {meeting.owner}, user_obj: {user_obj}")
        if not meeting.user_can_view(user_obj):
            raise MeetError("无权限查看此会议", BusinessCode.PERMISSION_DENIED)
            
        # 如果原函数期望meeting_id参数，确保传递
        sig = inspect.signature(view_func)
        if 'meetingid' in sig.parameters:
            kwargs['meetingid'] = meeting_id
            
        return view_func(request, *args, **kwargs)
    return wrapper


def require_meeting_owner(view_func):
    """要求会议所属人权限的装饰器（智能获取meeting_id）"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        meeting_id = _extract_meeting_id(request, *args, **kwargs)
        meeting = get_object_or_404(Meeting, id=meeting_id)
        request_user = get_user_info_from_token(request)
        user_obj = Users.objects.get(id=request_user.get('id'))
        print(f"meeting.owner: {meeting.owner}, user_obj: {user_obj}")
        if meeting.owner != user_obj:
            raise MeetError("仅会议所属人可执行此操作", BusinessCode.PERMISSION_DENIED)
            
        # 如果原函数期望meeting_id参数，确保传递
        sig = inspect.signature(view_func)
        if 'meetingid' in sig.parameters:
            kwargs['meetingid'] = meeting_id
            
        return view_func(request, *args, **kwargs)
    return wrapper


def get_user_meetings_queryset(user):
    """获取用户可访问的会议查询集"""
    from django.db.models import Q
    return Meeting.objects.filter(
        Q(owner=user) | Q(shares__shared_user=user, shares__is_active=True)
    ).distinct()