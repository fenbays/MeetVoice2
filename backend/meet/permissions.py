"""
会议权限装饰器
Linus原则：简单、直接、无废话
"""
import json
import inspect
from functools import wraps
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
from meet.models import Meeting, MeetingPhoto, Recording, Segment, Speaker
from system.models import Users
from utils.usual import get_user_info_from_token
from utils.meet_response import MeetError, BusinessCode


def _get_meeting_from_request(request, *args, **kwargs):
    """
    智能从请求中获取meeting对象
    支持以下参数：
    1. meetingid: 直接获取meeting
    2. recordingid: 通过recording关联获取meeting
    3. speakerid: 通过speaker->recording关联获取meeting
    """
    # 尝试从各种来源获取ID
    def get_id_from_sources(id_names):
        """从多个来源尝试获取ID"""
        for name in id_names:
            # 1. 从URL路径参数获取
            if name in kwargs:
                return name, kwargs[name]
            
            # 2. 从请求体获取
            if request.method in ['POST']:
                try:
                    if not hasattr(request, 'body') or not request.body:
                        raise MeetError("POST请求必须包含JSON格式的请求体", BusinessCode.BUSINESS_ERROR.value)
                    
                    body = json.loads(request.body)
                    if not isinstance(body, dict):
                        raise MeetError("请求体必须是JSON对象格式", BusinessCode.BUSINESS_ERROR.value)
                    
                    if name in body:
                        return name, body[name]
                    # 支持id字段（用于更新操作）
                    if name.endswith('id') and 'id' in body:
                        return name, body['id']
                        
                except json.JSONDecodeError:
                    raise MeetError("请求体格式错误，必须是有效的JSON", BusinessCode.BUSINESS_ERROR.value)
                except AttributeError:
                    raise MeetError("请求体不可访问", BusinessCode.BUSINESS_ERROR.value)
            
            # 3. 从查询参数获取
            if hasattr(request, 'GET') and name in request.GET:
                return name, request.GET[name]
        
        return None, None

    # 按优先级尝试不同的ID
    id_type, id_value = get_id_from_sources(['meetingid', 'meeting_id', 
                                           'recordingid', 'recording_id',
                                           'speakerid', 'speaker_id', 
                                           'segmentid', 'segment_id',
                                           'photoid', 'photo_id'
                                           ])
    
    if not id_value:
        raise MeetError("缺少必要的ID参数", BusinessCode.BUSINESS_ERROR.value)
    
    try:
        id_value = int(id_value)
    except (ValueError, TypeError):
        raise MeetError(f"{id_type}必须为数字", BusinessCode.BUSINESS_ERROR.value)

    # 根据ID类型获取meeting
    try:
        if id_type in ['meetingid', 'meeting_id']:
            meeting = Meeting.objects.get(id=id_value)
            kwargs['meeting'] = meeting
            return meeting
            
        elif id_type in ['recordingid', 'recording_id']:
            recording = Recording.objects.select_related('meeting').get(id=id_value)
            kwargs['recording'] = recording
            return recording.meeting
            
        elif id_type in ['speakerid', 'speaker_id']:
            speaker = Speaker.objects.select_related('recording__meeting').get(id=id_value)
            kwargs['speaker'] = speaker
            return speaker.recording.meeting
        
        elif id_type in ['segmentid', 'segment_id']:
            segment = Segment.objects.select_related('recording__meeting').get(id=id_value)
            kwargs['segment'] = segment
            return segment.recording.meeting
        
        elif id_type in ['photoid', 'photo_id']:
            photo = MeetingPhoto.objects.select_related('meeting').get(id=id_value)
            kwargs['photo'] = photo
            return photo.meeting
            
    except ObjectDoesNotExist:
        raise MeetError("请求的资源不存在", BusinessCode.INSTANCE_NOT_FOUND.value)


def require_meeting_permission(permission_type='view'):
    """
    统一的会议权限装饰器
    :param permission_type: 权限类型，可选值：'view'（查看权限）, 'edit'（编辑权限）, 'owner'（所有者权限）
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            meeting = _get_meeting_from_request(request, *args, **kwargs)
            request_user = get_user_info_from_token(request)
            user_obj = Users.objects.get(id=request_user.get('id'))
            
            if permission_type == 'view':
                if not meeting.user_can_view(user_obj):
                    raise MeetError("无权限查看此资源", BusinessCode.PERMISSION_DENIED.value)
            elif permission_type == 'edit':
                if not meeting.user_can_edit(user_obj):
                    raise MeetError("无权限编辑此资源", BusinessCode.PERMISSION_DENIED.value)
            elif permission_type == 'owner':
                if meeting.owner != user_obj:
                    raise MeetError("仅资源所有者可执行此操作", BusinessCode.PERMISSION_DENIED.value)
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# 为了向后兼容，保留原有的装饰器名称
require_meeting_view_permission = require_meeting_permission('view')
require_meeting_edit_permission = require_meeting_permission('edit')
require_meeting_owner = require_meeting_permission('owner')

# 录音和说话人的权限装饰器直接使用会议的权限
require_recording_view_permission = require_meeting_permission('view')
require_recording_owner = require_meeting_permission('owner')
require_speaker_view_permission = require_meeting_permission('view')
require_speaker_edit_permission = require_meeting_permission('edit')