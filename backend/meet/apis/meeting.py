from typing import List, Optional, Dict, Any
from datetime import datetime
import traceback

from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import QuerySet
from django.contrib.auth import get_user_model
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from pydantic import computed_field
from meet.apis.recording import RecordingSchemaOut, SpeakerSchemaOut, SegmentSchemaOut
from utils.usual import get_user_info_from_token
from utils.anti_duplicate import anti_duplicate
from system.models import File
from meet.models import Meeting, Recording, Speaker, Segment, MeetingShare, MeetingSummary, MeetingParticipant, MeetingPhoto
from utils.meet_crud import create, delete, retrieve, update
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import MeetResponse, MeetError, BusinessCode
from meet.permissions import (
    require_meeting_edit_permission,
    require_meeting_permission, 
    require_meeting_view_permission,
    require_meeting_owner,
)
from utils.meet_response import MeetResponse
import logging
import os

User = get_user_model()

router = Router()
logger = logging.getLogger(__name__)

def get_user_meetings_queryset(user_obj)->QuerySet[Meeting]:
    """
    获取用户可访问的会议QuerySet
    
    包含用户拥有的会议和被分享给用户的会议，按创建时间倒序排列。
    
    Args:
        user_obj: 用户对象实例
        
    Returns:
        QuerySet[Meeting]: 用户可访问的会议查询集，按创建时间倒序
        
    Raises:
        ValueError: 当用户对象为None时
        
    Example:
        >>> user = User.objects.get(id=1)
        >>> meetings = get_user_meetings_queryset(user)
        >>> print(meetings.count())
    """
    if not user_obj:
        raise ValueError("用户对象不能为None")
    
    # 获取用户拥有的会议ID
    owned_meeting_ids = Meeting.objects.filter(owner=user_obj).values_list('id', flat=True)
    
    # 获取分享给用户的会议ID
    shared_meeting_ids = MeetingShare.objects.filter(
        shared_user=user_obj, 
        is_active=True
    ).values_list('meeting_id', flat=True)
    
    # 合并所有会议ID
    all_meeting_ids = list(owned_meeting_ids) + list(shared_meeting_ids)
    
    # 返回合并后的QuerySet，按创建时间倒序
    return Meeting.objects.filter(id__in=all_meeting_ids).order_by('-create_datetime')


# ============= Meeting 相关接口 =============

class MeetingFilters(MeetFilters):
    title: Optional[str] = Field(None, alias="title")
    status: Optional[int] = Field(None, alias="status")
    start_time__gte: Optional[datetime] = Field(None, alias="start_time__gte")
    start_time__lte: Optional[datetime] = Field(None, alias="start_time__lte")


class MeetingSchemaIn(ModelSchema):
    id: Optional[int] = Field(None, alias="meetingid")
    class Config:
        model = Meeting
        model_fields = ['title','description','location_name','latitude','longitude','start_time','end_time','keywords','status']


class MeetingSchemaOut(ModelSchema):
    meetingid: int = Field(..., alias="id")
    class Config:
        model = Meeting
        model_exclude = ["id"]

    @computed_field(description="会议状态")
    def status_text(self) -> str | None:
        if self.status is not None:
            # 从 PROCESS_STATUS_CHOICES 中获取对应的显示文本
            for status_value, status_display in Meeting.STATUS_CHOICES:
                if status_value == self.status:
                    return status_display
        return None

class UserSchemaOut(ModelSchema):
    userid: int = Field(..., alias="id")
    class Config:
        model = User
        model_fields = ['id','name', 'avatar', 'email', 'mobile']


@router.post("/meeting/create", response=MeetingSchemaOut)
@anti_duplicate(expire_time=10)
def create_meeting(request, data: MeetingSchemaIn):
    """创建新会议"""
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(User, id=request_user['id'])
    data_dict = data.dict()
    data_dict['owner'] = user_obj
    try:
        meeting = create(request, data_dict, Meeting)
    except Exception as e:
        traceback.print_exc()
        logger.error(f'创建会议失败: {e}')
        return MeetResponse(errcode=BusinessCode.SERVER_ERROR, errmsg='创建会议失败')
    return meeting

@require_meeting_view_permission
@router.get("/meeting/get", response=MeetingSchemaOut)
def get_meeting(request, meetingid: int=Query(...)):
    """获取会议详情（需要查看权限）"""

    meeting = get_object_or_404(Meeting, id=meetingid)
    return meeting

@router.post("/meeting/update", response=MeetingSchemaOut)
@require_meeting_permission('owner')
def update_meeting(request, data: MeetingSchemaIn):
    """更新会议信息（需要编辑权限）"""
    meeting = update(request, data.id, data, Meeting)
    return meeting

@router.post("/meeting/list", response=List[MeetingSchemaOut])
@paginate(MyPagination)
def list_meeting(request, filters: MeetingFilters):
    """获取用户可访问的会议列表"""
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(User, id=request_user['id'])
    
    # 使用已有的函数获取用户可访问的会议
    qs = get_user_meetings_queryset(user_obj)
    
    # 应用过滤器
    if filters is not None:
        # 将filters中的空字符串值转换为None
        for attr, value in filters.__dict__.items():
            if getattr(filters, attr) == '':
                setattr(filters, attr, None)
        
        # 构建过滤条件字典
        filter_dict = filters.dict(exclude_none=True)
        
        # 特殊处理 title 字段为模糊搜索
        if 'title' in filter_dict:
            title_value = filter_dict.pop('title')
            qs = qs.filter(title__icontains=title_value)
        
        # 应用其他过滤条件
        if filter_dict:
            qs = qs.filter(**filter_dict)
    
    return qs

class MeetingDeleteSchemaIn(Schema):
    """删除会议Schema"""
    meetingid: int = Field(..., description="会议ID")

@router.post("/meeting/delete")
@require_meeting_permission('owner')
def delete_meeting(request, data: MeetingDeleteSchemaIn):
    """删除会议（需要编辑权限）"""
    delete(data.meetingid, Meeting)
    return MeetResponse(errcode=BusinessCode.OK)


class EnumItemSchema(Schema):
    value: str | int
    label: str


# 枚举注册表（统一管理）
ENUM_REGISTRY: Dict[str, list[tuple[Any, Any]]] = {
    "meeting-statuses": Meeting.STATUS_CHOICES,
    "photo-types": MeetingPhoto.PHOTO_TYPE_CHOICES,
}

@router.get("/enums/{enum_name}", response=List[EnumItemSchema])
def get_enum_items(request, enum_name: str):
    """
    获取指定枚举
    """
    if enum_name not in ENUM_REGISTRY:
        return []  # 或抛出 404 错误

    choices = ENUM_REGISTRY[enum_name]
    return [EnumItemSchema(value=c[0], label=c[1]) for c in choices]


@router.get("/enums")
def get_all_enums(request):
    """
    获取所有枚举
    """
    data = {}
    for key, choices in ENUM_REGISTRY.items():
        data[key] = [{"value": c[0], "label": c[1]} for c in choices]
    return {"data": data}

# ========== 会议分享 ==========

class MeetingShareSchemaIn(Schema):
    meetingid: int = Field(..., description="会议ID")
    userid_list: List[int] = Field(..., description="被分享用户ID列表")

class CancelShareSchemaIn(Schema):
    meetingid: int = Field(..., description="会议ID")
    userid_list: List[int] = Field(..., description="用户ID列表")

class MeetingShareSchemaOut(Schema):
    shareid: int = Field(...,  description="分享ID")
    meetingid: int = Field(..., description="会议ID") 
    shared_user: UserSchemaOut = Field(..., description="被分享用户")
    is_active: bool = Field(..., description="是否激活")
    create_datetime: datetime = Field(..., description="创建时间")

class MeetingBatchShareResponse(Schema):
    success_count: int = Field(..., description="成功分享数量")
    failure_count: int = Field(..., description="失败分享数量")
    success_shares: List[MeetingShareSchemaOut] = Field(..., description="成功的分享")
    failed_users: List[dict] = Field(..., description="失败的用户信息")

class UserMeetingIdSchemaOut(Schema):
    shareid: Optional[int]  # 如果是shared会议才有
    meetingid: int
    userid: int
    is_active: bool
    create_datetime: datetime
    meeting_type: Optional[str]  # 新增字段，标识是 'owned' 还是 'shared'

@router.post("/meeting/share", response=MeetingBatchShareResponse)
@require_meeting_owner
def share_meeting(request, data: MeetingShareSchemaIn):
    """分享会议给指定用户"""
    request_user = get_user_info_from_token(request)
    request_user_id = request_user['id']
    
    # 数据预处理
    unique_user_ids = list(set(data.userid_list))
    if request_user_id in unique_user_ids:
        unique_user_ids.remove(request_user_id)
    
    if not unique_user_ids:
        raise MeetError("没有有效的分享对象", BusinessCode.BUSINESS_ERROR.value)

    # 批量查询用户，提高性能
    existing_users = User.objects.filter(
        id__in=unique_user_ids, 
        is_active=True
    ).values_list('id', flat=True)
    
    existing_user_ids = set(existing_users)
    missing_user_ids = set(unique_user_ids) - existing_user_ids
    
    success_shares = []
    failed_users = []
    
    # 批量处理存在的用户
    if existing_user_ids:
        with transaction.atomic():
            for user_id in existing_user_ids:
                try:
                    user = User.objects.get(id=user_id)
                    share, created = MeetingShare.objects.get_or_create(
                        meeting_id=data.meetingid,
                        shared_user=user,
                        defaults={'is_active': True}
                    )
                    
                    if not created and not share.is_active:
                        share.is_active = True
                        share.save()
                    
                    success_shares.append(MeetingShareSchemaOut(
                        shareid=share.id,
                        meetingid=share.meeting.id,
                        shared_user=UserSchemaOut.from_orm(share.shared_user),
                        is_active=share.is_active,
                        create_datetime=share.create_datetime
                    ).dict())
                    
                except Exception as e:
                    failed_users.append({
                        "user_id": user_id,
                        "reason": f"分享失败: {str(e)}",
                        "share_id": None
                    })
    
    # 处理不存在的用户
    for user_id in missing_user_ids:
        failed_users.append({
            "user_id": user_id,
            "reason": "用户不存在或已被禁用",
            "share_id": None
        })

    return MeetResponse(data={
        "success_count": len(success_shares),
        "failure_count": len(failed_users),
        "success_shares": success_shares,
        "failed_shares": failed_users
    }, errcode=BusinessCode.OK)
    

@require_meeting_edit_permission
@router.post("/meeting/cancel_share")
def cancel_share_meeting(request, data: CancelShareSchemaIn):
    """取消分享会议"""
    shares = MeetingShare.objects.filter(
        meeting_id=data.meetingid, 
        shared_user_id__in=data.userid_list,
        is_active=True
    )

    if not shares.exists():
        return MeetResponse(
            errcode=BusinessCode.INSTANCE_NOT_FOUND, 
            errmsg="没有找到有效的分享记录", 
        )
    
    # 获取成功取消的用户ID列表
    canceled_user_ids = list(shares.values_list('shared_user__id', flat=True))

     # 批量更新为非激活状态
    updated_count = shares.update(is_active=False)

    return MeetResponse(
        errcode=BusinessCode.OK, 
        errmsg="批量取消分享完成", 
        data={
            "meeting_id": data.meetingid,
            "canceled_user_ids": canceled_user_ids
        }
    )


@router.get("/meeting/share/list", response=List[UserMeetingIdSchemaOut])
@require_meeting_owner
@paginate(MyPagination)
def list_meeting_shares(request, meetingid: int=Query(...)):
    """获取会议的所有分享用户列表"""
    shares = MeetingShare.objects.filter(
        meeting_id=meetingid, 
        is_active=True
    ).select_related('shared_user').order_by('-create_datetime')
    
    return [UserMeetingIdSchemaOut(
            shareid=share.id,
            userid=share.shared_user.id,
            meetingid = share.meeting_id,
            is_active=share.is_active,
            create_datetime=share.create_datetime,
            meeting_type="shared"
        ).dict() for share in shares
    ]

@router.get("/meeting/share/get_user_meetingid", response=List[UserMeetingIdSchemaOut])
@paginate(MyPagination)
def get_user_meetingid(request, userid: int=Query(...)):
    """获取成员的会议ID列表（包含拥有的owned和被分享的shared）"""
    request_user_info = get_user_info_from_token(request)
    request_user_id = request_user_info['id']
    
    if request_user_id != userid:
        raise MeetError("无权限访问", BusinessCode.PERMISSION_DENIED.value)
    logger.info(f"/meeting/share/get_user_meetingid request_user_id: {request_user_id}")
    
    req_user_obj = get_object_or_404(User, id=request_user_id)
    
    # 获取用户拥有的会议
    owned_meetings = Meeting.objects.filter(
        owner=req_user_obj
    ).order_by('-create_datetime')
    
    # 获取分享给用户的会议
    shares = MeetingShare.objects.filter(
        shared_user=req_user_obj, 
        is_active=True
    ).select_related('meeting').order_by('-create_datetime')
    
    # 合并结果
    result = []
    
    # 添加owned会议
    for meeting in owned_meetings:
        result.append(
            UserMeetingIdSchemaOut(
                shareid=None,  # owned会议没有shareid
                meetingid=meeting.id,
                userid=request_user_id,
                is_active=True,  # owned会议总是active
                create_datetime=meeting.create_datetime,
                meeting_type='owned'
            ).dict()
        )
    
    # 添加shared会议
    for share in shares:
        result.append(
            UserMeetingIdSchemaOut(
                shareid=share.id,
                meetingid=share.meeting.id,
                userid=share.shared_user.id,
                is_active=share.is_active,
                create_datetime=share.create_datetime,
                meeting_type='shared'
            ).dict()
        )
    
    # 按创建时间倒序排序
    result.sort(key=lambda x: x['create_datetime'], reverse=True)
    return result


# ============= MeetingSummary 会议纲要相关接口 =============

class SummaryFilters(MeetFilters):
    meeting_id: int = Field(None, alias="meeting_id")
    generate_status: int = Field(None, alias="generate_status")


class SummarySchemaIn(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    
    class Config:
        model = MeetingSummary
        model_exclude = ['id', 'meeting', 'create_datetime', 'update_datetime']


class SummarySchemaOut(ModelSchema):
    class Config:
        model = MeetingSummary
        model_fields = "__all__"

@router.get("/summary", response=List[SummarySchemaOut])
def list_summary(request, filters: SummaryFilters = Query(...)):
    """获取会议纲要列表"""
    qs = retrieve(request, MeetingSummary, filters)
    return qs


@router.get("/meeting/{meeting_id}/summary", response=SummarySchemaOut)
def get_meeting_summary(request, meeting_id: int):
    """获取指定会议的纲要"""
    summary = get_object_or_404(MeetingSummary, meeting_id=meeting_id)
    return summary


# ============= MeetingParticipant 会议参与人相关接口 =============

class ParticipantFilters(MeetFilters):
    meeting_id: int = Field(None, alias="meeting_id")
    is_moderator: bool = Field(None, alias="is_moderator")
    user_id: int = Field(None, alias="user_id")


class ParticipantSchemaIn(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    user_id: int = Field(None, description="关联用户ID（可选）")
    
    class Config:
        model = MeetingParticipant
        model_exclude = ['id', 'meeting', 'user', 'create_datetime', 'update_datetime']


class ParticipantSchemaOut(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    user_id: int = Field(None, description="关联用户ID")
    user_name: str = Field(None, description="关联用户姓名")
    
    class Config:
        model = MeetingParticipant
        model_fields = "__all__"
    
    @staticmethod
    def resolve_user_name(obj):
        return obj.user.name if obj.user else None


@require_meeting_edit_permission
@router.post("/meeting/{meeting_id}/participant", response=ParticipantSchemaOut)
def add_participant(request, meeting_id: int, data: ParticipantSchemaIn):
    """添加参会人员（需要编辑权限）"""
    participant_data = data.dict()
    participant_data['meeting_id'] = meeting_id
    
    # 处理用户关联
    if 'user_id' in participant_data and participant_data['user_id']:
        user = get_object_or_404(User, id=participant_data['user_id'])
        participant_data['user'] = user
        # 如果关联了用户但没填姓名，自动填充
        if not participant_data.get('name'):
            participant_data['name'] = user.name
    
    participant_data.pop('user_id', None)
    participant = create(request, participant_data, MeetingParticipant)
    return participant


@require_meeting_edit_permission
@router.delete("/meeting/{meeting_id}/participant/{participant_id}")
def remove_participant(request, meeting_id: int, participant_id: int):
    """移除参会人员（需要编辑权限）"""
    participant = get_object_or_404(MeetingParticipant, 
                                   id=participant_id, 
                                   meeting_id=meeting_id)
    participant.delete()
    return {"success": True}


@require_meeting_edit_permission
@router.put("/meeting/{meeting_id}/participant/{participant_id}", response=ParticipantSchemaOut)
def update_participant(request, meeting_id: int, participant_id: int, data: ParticipantSchemaIn):
    """更新参会人员信息（需要编辑权限）"""
    participant_data = data.dict()
    
    # 处理用户关联
    if 'user_id' in participant_data:
        if participant_data['user_id']:
            user = get_object_or_404(User, id=participant_data['user_id'])
            participant_data['user'] = user
        else:
            participant_data['user'] = None
        participant_data.pop('user_id')
    
    participant = update(request, participant_id, participant_data, MeetingParticipant)
    return participant


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/participants", response=List[ParticipantSchemaOut])
def list_meeting_participants(request, meeting_id: int):
    """获取会议参会人员列表（需要查看权限）"""
    participants = MeetingParticipant.objects.filter(
        meeting_id=meeting_id
    ).select_related('user').order_by('-is_moderator', 'name')
    return list(participants)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/moderator", response=ParticipantSchemaOut)
def get_meeting_moderator(request, meeting_id: int):
    """获取会议主持人（需要查看权限）"""
    moderator = get_object_or_404(MeetingParticipant, 
                                 meeting_id=meeting_id, 
                                 is_moderator=True)
    return moderator


# ============= MeetingPhoto 会议图片相关接口 =============

class PhotoFilters(MeetFilters):
    meeting_id: int = Field(None, alias="meeting_id")
    photo_type: int = Field(None, alias="photo_type")


class PhotoSchemaIn(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    file_id: int = Field(..., description="图片文件ID")
    
    class Config:
        model = MeetingPhoto
        model_exclude = ['id', 'meeting', 'file', 'create_datetime', 'update_datetime']


class PhotoSchemaOut(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    file_id: int = Field(..., description="图片文件ID")
    file_url: str = Field(None, description="图片URL")
    file_name: str = Field(None, description="文件名")
    file_size: int = Field(None, description="文件大小（字节）")
    photo_type_display: str = Field(None, description="照片类型显示名")
    
    class Config:
        model = MeetingPhoto
        model_fields = "__all__"
    
    @staticmethod
    def resolve_file_url(obj):
        return obj.file.get_absolute_url() if obj.file else None
    
    @staticmethod
    def resolve_file_name(obj):
        return obj.file.name if obj.file else None
    
    @staticmethod
    def resolve_file_size(obj):
        return obj.file.size if obj.file else None
    
    @staticmethod
    def resolve_photo_type_display(obj):
        return obj.get_photo_type_display()


class PhotoUpdateSchemaIn(Schema):
    """照片更新Schema，仅包含可修改字段"""
    photo_type: int = Field(..., description="照片类型")
    description: str = Field(None, description="照片描述")


class BatchPhotoDeleteSchema(Schema):
    """批量删除照片Schema"""
    photo_ids: List[int] = Field(..., description="照片ID列表")


@require_meeting_edit_permission
@router.post("/meeting/{meeting_id}/photo", response=PhotoSchemaOut)
def upload_meeting_photo(request, meeting_id: int, data: PhotoSchemaIn):
    """上传会议照片（需要编辑权限）"""
    try:
        photo_data = data.dict()
        photo_data['meeting_id'] = meeting_id
        
        # 验证文件存在且是图片类型
        file = get_object_or_404(File, id=photo_data['file_id'])
        
        # 验证文件类型（可选：加强安全性）
        allowed_image_types = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']
        file_ext = file.save_name.split('.')[-1].lower() if file.save_name else ''
        if file_ext not in allowed_image_types:
            raise MeetError("文件类型不支持，请上传图片文件", BusinessCode.BUSINESS_ERROR)
        
        # 验证照片类型有效性
        photo_type = photo_data.get('photo_type')
        valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
        if photo_type not in valid_types:
            raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR)
        
        photo_data['file_id'] = photo_data.pop('file_id')
        photo = create(request, photo_data, MeetingPhoto)
        return photo
        
    except Exception as e:
        logger.error(f"上传会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("上传照片失败", BusinessCode.SERVER_ERROR)


@require_meeting_edit_permission
@router.delete("/meeting/{meeting_id}/photo/{photo_id}")
def delete_meeting_photo(request, meeting_id: int, photo_id: int):
    """删除会议照片（需要编辑权限）"""
    try:
        photo = get_object_or_404(MeetingPhoto, 
                                 id=photo_id, 
                                 meeting_id=meeting_id)
        
        # 记录删除的文件信息用于日志
        file_name = photo.file.name if photo.file else "未知"
        photo_type = photo.get_photo_type_display()
        
        photo.delete()
        
        logger.info(f"删除会议照片成功 - 会议ID: {meeting_id}, 照片: {file_name}, 类型: {photo_type}")
        return {"success": True, "message": "照片删除成功"}
        
    except Exception as e:
        logger.error(f"删除会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("删除照片失败", BusinessCode.SERVER_ERROR)


@require_meeting_edit_permission
@router.post("/meeting/{meeting_id}/photos/batch-delete")
def batch_delete_meeting_photos(request, meeting_id: int, data: BatchPhotoDeleteSchema):
    """批量删除会议照片（需要编辑权限）"""
    try:
        photo_ids = data.photo_ids
        if not photo_ids:
            raise MeetError("请选择要删除的照片", BusinessCode.BUSINESS_ERROR)
        
        # 验证所有照片都属于该会议
        photos = MeetingPhoto.objects.filter(
            id__in=photo_ids, 
            meeting_id=meeting_id
        )
        
        if len(photos) != len(photo_ids):
            raise MeetError("部分照片不存在或不属于该会议", BusinessCode.BUSINESS_ERROR)
        
        deleted_count = len(photos)
        photos.delete()
        
        logger.info(f"批量删除会议照片成功 - 会议ID: {meeting_id}, 删除数量: {deleted_count}")
        return {
            "success": True, 
            "message": f"成功删除 {deleted_count} 张照片",
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        logger.error(f"批量删除会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("批量删除照片失败", BusinessCode.SERVER_ERROR)


@require_meeting_edit_permission
@router.put("/meeting/{meeting_id}/photo/{photo_id}", response=PhotoSchemaOut)
def update_meeting_photo(request, meeting_id: int, photo_id: int, data: PhotoUpdateSchemaIn):
    """更新会议照片信息（需要编辑权限）"""
    try:
        photo = get_object_or_404(MeetingPhoto, 
                                 id=photo_id, 
                                 meeting_id=meeting_id)
        
        photo_data = data.dict(exclude_unset=True)  # 只更新提供的字段
        
        # 验证照片类型有效性
        if 'photo_type' in photo_data:
            valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
            if photo_data['photo_type'] not in valid_types:
                raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR)
        
        # 更新字段
        for field, value in photo_data.items():
            setattr(photo, field, value)
        
        photo.save()
        return photo
        
    except Exception as e:
        logger.error(f"更新会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("更新照片信息失败", BusinessCode.SERVER_ERROR)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photos", response=List[PhotoSchemaOut])
def list_meeting_photos(request, meeting_id: int, photo_type: int = None):
    """获取会议照片列表（需要查看权限）"""
    try:
        queryset = MeetingPhoto.objects.filter(meeting_id=meeting_id)
        
        if photo_type is not None:
            # 验证照片类型有效性
            valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
            if photo_type not in valid_types:
                raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR)
            queryset = queryset.filter(photo_type=photo_type)
        
        photos = queryset.select_related('file').order_by('photo_type', '-create_datetime')
        return list(photos)
        
    except Exception as e:
        logger.error(f"获取会议照片列表失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("获取照片列表失败", BusinessCode.SERVER_ERROR)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photos/meeting", response=List[PhotoSchemaOut])
def list_meeting_photos_only(request, meeting_id: int):
    """获取会议照片（不包含签到表）"""
    return list_meeting_photos(request, meeting_id, photo_type=1)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photos/signin", response=List[PhotoSchemaOut])
def list_signin_photos(request, meeting_id: int):
    """获取签到表照片"""
    return list_meeting_photos(request, meeting_id, photo_type=2)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photo/{photo_id}", response=PhotoSchemaOut)
def get_meeting_photo_detail(request, meeting_id: int, photo_id: int):
    """获取单张照片详情（需要查看权限）"""
    try:
        photo = get_object_or_404(MeetingPhoto, 
                                 id=photo_id, 
                                 meeting_id=meeting_id)
        return photo
        
    except Exception as e:
        logger.error(f"获取照片详情失败: {str(e)}")
        raise MeetError("获取照片详情失败", BusinessCode.SERVER_ERROR)


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photos/count")
def get_meeting_photos_count(request, meeting_id: int):
    """获取会议照片统计信息"""
    try:
        total_count = MeetingPhoto.objects.filter(meeting_id=meeting_id).count()
        meeting_photos_count = MeetingPhoto.objects.filter(
            meeting_id=meeting_id, 
            photo_type=1
        ).count()
        signin_photos_count = MeetingPhoto.objects.filter(
            meeting_id=meeting_id, 
            photo_type=2
        ).count()
        
        return {
            "total_count": total_count,
            "meeting_photos_count": meeting_photos_count,
            "signin_photos_count": signin_photos_count,
            "breakdown": {
                "会议照片": meeting_photos_count,
                "签到表": signin_photos_count
            }
        }
        
    except Exception as e:
        logger.error(f"获取照片统计失败: {str(e)}")
        raise MeetError("获取照片统计失败", BusinessCode.SERVER_ERROR)


# ============= 聚合接口 =============

class MeetingDetailSchema(Schema):
    meeting: MeetingSchemaOut
    participants: List[ParticipantSchemaOut] = []
    photos: List[PhotoSchemaOut] = []
    recordings: List[RecordingSchemaOut] = []
    summary: SummarySchemaOut = None


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/detail")
def get_meeting_detail(request, meeting_id: int):
    """获取会议完整详情（包含参会人员、照片、录音和纲要）"""
    meeting = get_object_or_404(Meeting, id=meeting_id)
    
    # 获取参会人员
    participants = list(MeetingParticipant.objects.filter(
        meeting_id=meeting_id
    ).select_related('user').order_by('-is_moderator', 'name'))
    
    # 获取照片
    photos = list(MeetingPhoto.objects.filter(
        meeting_id=meeting_id
    ).select_related('file').order_by('photo_type', '-create_datetime'))
    
    # 获取录音
    recordings = list(Recording.objects.filter(meeting_id=meeting_id))
    
    # 获取纲要
    try:
        summary = MeetingSummary.objects.get(meeting_id=meeting_id)
    except MeetingSummary.DoesNotExist:
        summary = None
    
    return MeetResponse(data={
        "meeting": meeting,
        "participants": participants,
        "photos": photos, 
        "recordings": recordings,
        "summary": summary
    })


class RecordingDetailSchema(Schema):
    recording: RecordingSchemaOut
    speakers: List[SpeakerSchemaOut]
    transcripts: List[SegmentSchemaOut]


@router.get("/recording/{recording_id}/detail")
def get_recording_detail(request, recording_id: int):
    """获取录音完整详情（包含说话人和转录）"""
    recording = get_object_or_404(Recording, id=recording_id)
    speakers = list(Speaker.objects.filter(recording_id=recording_id))
    transcripts = list(Segment.objects.filter(recording_id=recording_id).order_by('start_time'))
    
    return MeetResponse(data={
        "recording": recording,
        "speakers": speakers,
        "transcripts": transcripts
    })
