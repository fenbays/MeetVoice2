from functools import cached_property
from typing import List, Optional, Dict, Any
from datetime import datetime
import traceback

from django.shortcuts import get_object_or_404
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.contrib.auth import get_user_model
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from pydantic import ValidationError, computed_field, field_validator, model_validator
from utils.meet_auth import data_permission
from meet.tasks import generate_meeting_report_task
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
    owned_meeting_ids = Meeting.objects.filter(owner=user_obj, delete_status=0).values_list('id', flat=True)
    
    # 获取分享给用户的会议ID
    shared_meeting_ids = MeetingShare.objects.filter(
        shared_user=user_obj, 
        is_active=True,
        meeting__delete_status=0
    ).values_list('meeting_id', flat=True)
    
    # 合并所有会议ID
    all_meeting_ids = list(owned_meeting_ids) + list(shared_meeting_ids)
    
    # 返回合并后的QuerySet，按创建时间倒序
    return Meeting.objects.filter(id__in=all_meeting_ids, delete_status=0).order_by('-create_datetime')


# ============= Meeting 相关接口 =============
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

class MeetingParticipantSchemaOut(ModelSchema):
    """参会人员Schema"""
    participantid: int = Field(..., alias="id")
    class Config:
        model = MeetingParticipant
        model_exclude = ["id"]

class PhotoSchemaOut(ModelSchema):
    id: int = Field(..., exclude=True)
    photoid: int = Field(..., description="关联会议ID", alias="id")
    
    class Config:
        model = MeetingPhoto
        model_exclude = ['remark']
    
    @cached_property
    def _file_obj(self):
        return File.objects.filter(id=self.file).first() if self.file else None

    @cached_property
    def _photo_obj(self):
        return MeetingPhoto.objects.filter(id=self.id).first() if self.id else None

    @computed_field
    def file_uuid(self) -> str:
        return str(self._file_obj.uuid) if self._file_obj else None

    @computed_field
    def file_url(self) -> str:
        return self._file_obj.get_absolute_url() if self._file_obj else None

    @computed_field
    def file_name(self) -> str:
        return self._file_obj.name if self._file_obj else None

    @computed_field
    def file_size(self) -> int:
        return self._file_obj.size if self._file_obj else None

    @computed_field
    def photo_type_display(self) -> str:
        return self._photo_obj.get_photo_type_display() if self._photo_obj else None

class MeetingDetailSchemaOut(ModelSchema):
    """会议详情Schema - 包含所有相关信息"""
    meetingid: int = Field(..., alias="id")
    participants: List[MeetingParticipantSchemaOut] = Field(default=[], description="参会人员列表")
    photos: List[PhotoSchemaOut] = Field(default=[], description="所有会议照片")
    meeting_photos: List[PhotoSchemaOut] = Field(default=[], description="会议照片（非签到表）")
    signin_photos: List[PhotoSchemaOut] = Field(default=[], description="签到表照片")
    speakers: List[SpeakerSchemaOut] = Field(default=[], description="发言人列表")
    
    class Config:
        model = Meeting
        model_exclude = ["id"]
    
    @computed_field(description="会议状态")
    def status_text(self) -> str | None:
        if self.status is not None:
            for status_value, status_display in Meeting.STATUS_CHOICES:
                if status_value == self.status:
                    return status_display
        return None

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

@router.get("/meeting/get", response=MeetingDetailSchemaOut)
@require_meeting_permission('view')
def get_meeting(request, meetingid: int=Query(...)):
    """获取会议详情（需要查看权限）"""

    meeting = get_object_or_404(
    Meeting.objects.select_related('owner').prefetch_related(
            'participants',
            'photos__file', 
            'recordings__speakers'
        ), 
        id=meetingid,
        delete_status=0
    )
    # 获取参会人员
    participants = list(meeting.participants.all())
    
    # 获取所有照片
    all_photos = list(meeting.photos.all())
    
    # 分类照片：会议照片和签到表
    meeting_photos = [photo for photo in all_photos if photo.photo_type == 1]
    signin_photos = [photo for photo in all_photos if photo.photo_type == 2]
    
    # 获取所有发言人（从所有录音中）
    all_speakers = []
    for recording in meeting.recordings.all():
        all_speakers.extend(recording.speakers.all())
    
    # 构建返回数据
    meeting_dict = meeting.__dict__.copy()
    meeting_dict.update({
        'participants': participants,
        'photos': all_photos,
        'meeting_photos': meeting_photos,
        'signin_photos': signin_photos,
        'speakers': all_speakers,
    })
    
    return meeting_dict

@router.post("/meeting/update", response=MeetingSchemaOut)
@require_meeting_permission('owner')
def update_meeting(request, data: MeetingSchemaIn):
    """更新会议信息（需要编辑权限）"""
    try:
        meeting = Meeting.objects.get(id=data.id, delete_status=0)
    except Meeting.DoesNotExist:
        raise MeetError("会议不存在或已被删除", BusinessCode.INSTANCE_NOT_FOUND.value)
    
    meeting = update(request, data.id, data, Meeting)
    return meeting


class MeetingFilters(MeetFilters):
    title: Optional[str] = Field(None, alias="title")
    status: Optional[int] = Field(None, alias="status")
    start_time__gte: Optional[datetime] = Field(None, alias="start_time__gte")
    start_time__lte: Optional[datetime] = Field(None, alias="start_time__lte")

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
    delete_type: int = Field(1, description="删除类型：1=软删除(回收站)，2=硬删除(彻底删除)")
    reason: str = Field(None, description="删除原因")

@router.post("/meeting/delete")
@require_meeting_permission('owner')
def delete_meeting(request, data: MeetingDeleteSchemaIn):
    """删除会议（需要编辑权限）"""
    try:
        meeting = Meeting.objects.get(id=data.meetingid, delete_status=0)
    except Meeting.DoesNotExist:
        raise MeetError("会议不存在或已被删除", BusinessCode.INSTANCE_NOT_FOUND.value)
    
    if data.delete_type == 1:
        # 软删除：放入回收站
        meeting.soft_delete(reason=data.reason)
    elif data.delete_type == 2:
        # 硬删除：标记为彻底删除
        meeting.hard_delete(reason=data.reason)
    else:
        raise MeetError("无效的删除类型", BusinessCode.PARAMETER_ERROR.value)
    
    return MeetResponse(errcode=BusinessCode.OK)

@router.get("/meeting/trash", response=List[MeetingSchemaOut])
@paginate(MyPagination)
def list_trash_meetings(request):
    """获取回收站会议列表 - 只返回当前用户拥有的软删除会议"""
    
    # 先应用数据权限过滤，再过滤软删除状态
    qs = retrieve(request, Meeting).values().all()
    qs = qs.filter(delete_status=1)  # 只包含软删除的
    
    # 确保只返回当前用户拥有的会议
    user_info = get_user_info_from_token(request)
    user_obj = get_object_or_404(User, id=user_info['id'])
    if not user_info['is_superuser']:
        qs = qs.filter(owner=user_obj)
    
    qs = qs.order_by('-deleted_datetime')  # 按删除时间倒序
    return qs

@router.post("/meeting/restore", response=MeetingSchemaOut)
@require_meeting_permission('owner')
def restore_meeting(request, data: MeetingDeleteSchemaIn):
    """恢复会议"""
    try:
        meeting = Meeting.objects.get(id=data.meetingid, delete_status=1)
        meeting.restore()
    except Meeting.DoesNotExist:
        raise MeetError("会议不存在或不在回收站中", BusinessCode.INSTANCE_NOT_FOUND.value)
    
    return MeetResponse(errcode=BusinessCode.OK)

class EnumItemSchema(Schema):
    value: str | int
    label: str


# 枚举注册表（统一管理）
ENUM_REGISTRY: Dict[str, list[tuple[Any, Any]]] = {
    "meeting-statuses": Meeting.STATUS_CHOICES,
    "meeting-report-statuses": MeetingSummary.GENERATE_STATUS_CHOICES,
    "photo-types": MeetingPhoto.PHOTO_TYPE_CHOICES,
    "sex-types": User.GENDER_CHOICES,
    "delete-types": Meeting.DELETE_STATUS_CHOICES
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
    return {"items": data}

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

class SummarySchemaIn(ModelSchema):
    meetingid: int = Field(..., description="关联会议ID")
    
    class Config:
        model = MeetingSummary
        model_exclude = ['id', 'meeting']


class SummarySchemaOut(ModelSchema):
    summaryid: int = Field(..., description="纲要ID", alias="id")
    class Config:
        model = MeetingSummary
        model_exclude = ['id', 'meeting']

    @computed_field(description="会议报告文件")
    def generate_status_text(self) -> str:
        if self.generate_status is not None:
            # 从 PROCESS_STATUS_CHOICES 中获取对应的显示文本
            for status_value, status_display in MeetingSummary.GENERATE_STATUS_CHOICES:
                if status_value == self.generate_status:
                    return status_display
        return None

@router.get("/meeting/summary/get", response=SummarySchemaOut)
def get_meeting_summary(request, meetingid: int=Query(...)):
    """获取指定会议的纲要、会议报告文件"""
    summary = get_object_or_404(MeetingSummary, meeting_id=meetingid)
    return summary


@router.get("/meeting/reportfile/generate", response=SummarySchemaOut)
def generate_meeting_report(request, meetingid: int=Query(...)):
    """生成会议报告文件
    生成前提：会议名称、发言人、参会人员、会议照片、会议签到表均已设置
    """
    summary = get_object_or_404(MeetingSummary, meeting_id=meetingid)
    meeting = summary.meeting
    
    # 1. 检查必要信息是否完整
    if not meeting.title:
        raise MeetError("会议名称未设置", BusinessCode.BUSINESS_ERROR.value)
        
    # 检查主持人
    moderator = meeting.get_moderator()
    if not moderator:
        raise MeetError("未设置会议主持人", BusinessCode.BUSINESS_ERROR.value)
        
    # 检查参会人员
    participants = meeting.participants.all()
    if not participants.exists():
        raise MeetError("未添加参会人员", BusinessCode.BUSINESS_ERROR.value)
        
    # 检查会议照片
    photos = meeting.photos.filter(photo_type=1)
    if not photos.exists():
        raise MeetError("未上传会议照片", BusinessCode.BUSINESS_ERROR.value)
        
    # 检查签到表
    sign_in_sheets = meeting.photos.filter(photo_type=2)
    if not sign_in_sheets.exists():
        raise MeetError("未上传签到表", BusinessCode.BUSINESS_ERROR.value)
    
    # 检查当前状态
    if summary.generate_status == 1:
        return summary
        
    # 更新状态为生成中
    summary.generate_status = 1
    summary.save()
    
    # 启动异步任务
    generate_meeting_report_task.delay(meetingid)
    
    return summary


# ============= MeetingParticipant 会议参与人相关接口 =============

class ParticipantFilters(MeetFilters):
    meetingid: int = Field(None, alias="meetingid", description="关联会议ID")
    is_moderator: Optional[bool] = Field(None, alias="is_moderator", description="是否主持人")
    participantid: Optional[int] = Field(None, alias="participantid", description="参会人员ID")



class ParticipantSchemaIn(ModelSchema):
    """参会人员输入Schema"""
    meetingid: int = Field(..., description="关联会议ID")
    participantid: Optional[int] = Field(None, description="参会人员ID")
    userid: Optional[int] = Field(None, description="关联用户ID（可选）")
    name: str = Field(..., description="姓名")
    company: Optional[str] = Field(None, description="单位")
    title: Optional[str] = Field(None, description="职务")
    is_moderator: Optional[bool] = Field(False, description="是否主持人")

    @model_validator(mode="after")
    def validate_with_model(self):
        from meet.models import MeetingParticipant
        instance = MeetingParticipant(
            id=self.participantid,
            meeting_id=self.meetingid,
            name=self.name,
            company=self.company,
            title=self.title,
            is_moderator=self.is_moderator,
        )
        instance.clean()
        return self
    
    class Config:
        model = MeetingParticipant
        model_exclude = ['user', 'meeting','create_datetime', 'update_datetime']


class ParticipantSchemaOut(ModelSchema):
    """参会人员输出Schema"""
    participantid: int = Field(..., description="参会人员ID", alias="id")
        
    class Config:
        model = MeetingParticipant
        model_exclude = ["id", "create_datetime", "update_datetime"]
    
    @computed_field(description="关联会议ID")
    def meetingid(self) -> int:
        return self.meeting if self.meeting else None
    
    @computed_field(description="关联用户ID")
    def userid(self) -> int | None:
        return self.user if self.user else None   
  

@require_meeting_permission('edit')
@router.post("/meeting/participant/add", response=ParticipantSchemaOut)
def add_participant(request, data: ParticipantSchemaIn):
    """添加参会人员（需要编辑权限）"""
    meeting = get_object_or_404(Meeting, id=data.meetingid)
    participant_data = data.dict()
    
    # 处理用户关联
    if participant_data.get('userid'):
        user = get_object_or_404(User, id=participant_data['userid'])
        participant_data['user'] = user
        # 如果关联了用户但没填姓名，自动填充
        if not participant_data.get('name'):
            participant_data['name'] = user.name if user.name else user.username
    else:
        participant_data['user'] = None
    
    participant_data['meeting'] = meeting
    participant_data.pop('userid', None)
    participant_data.pop('meetingid', None) 
    participant_data.pop('participantid', None)
    
    try:
        participant = create(request, participant_data, MeetingParticipant)
        return participant
    except ValidationError as e:
        # 捕获模型验证错误（包括业务规则验证）
        error_messages = []
        if hasattr(e, 'message_dict'):
            for field, messages in e.message_dict.items():
                error_messages.extend(messages)
        else:
            error_messages = [str(e)]
        raise MeetError(f"数据验证错误: {'; '.join(error_messages)}", BusinessCode.BUSINESS_ERROR.value)
    except IntegrityError as e:
        # 捕获重复添加的错误
        if "Duplicate entry" in str(e) and "meet_participant_meeting_id_name_company" in str(e):
            raise MeetError("该参会人员已存在，不能重复添加", BusinessCode.BUSINESS_ERROR.value)
        else:
            # 其他完整性错误
            raise MeetError(f"数据完整性错误: {str(e)}", BusinessCode.BUSINESS_ERROR.value)

class ParticipantDeleteSchemaIn(Schema):
    """移除参与人Schema"""
    # meetingid: int = Field(..., description="关联会议ID")
    participantid: int = Field(..., description="参会人员ID")

@require_meeting_permission('edit')
@router.post("/meeting/participant/delete")
def remove_participant(request, data: ParticipantDeleteSchemaIn):
    """移除参会人员（需要编辑权限）"""
    participant = get_object_or_404(MeetingParticipant, 
                                   id=data.participantid
                                   )
    participant.delete()
    return MeetResponse(errcode=BusinessCode.OK)


@require_meeting_permission('edit')
@router.post("/meeting/participant/update", response=ParticipantSchemaOut)
def update_participant(request, data: ParticipantSchemaIn):
    """更新参会人员信息（需要编辑权限）"""
    meeting = get_object_or_404(Meeting, id=data.meetingid)
    
    # 验证参会人员存在且属于指定会议
    participant = get_object_or_404(MeetingParticipant, 
                                   id=data.participantid, 
                                   meeting=meeting)
        
    # 处理用户关联
    user = None
    if data.userid:
        user = get_object_or_404(User, id=data.userid)
        # 如果关联了用户但没填姓名，自动填充
        if not data.name:
            data.name = user.name if user.name else user.username
    
    # 获取用户信息用于更新修改者字段
    user_info = get_user_info_from_token(request)
    
    try:
        participant.name = data.name
        participant.company = data.company
        participant.title = data.title
        participant.is_moderator = data.is_moderator if data.is_moderator is not None else False
        participant.user = user
        participant.modifier = user_info['name']  # 更新修改者信息
        
        participant.save()
        return participant
        
    except IntegrityError as e:
        if "Duplicate entry" in str(e) and "meet_participant_meeting_id_name_company" in str(e):
            raise MeetError("该参会人员信息已存在，不能更新", BusinessCode.BUSINESS_ERROR.value)
        else:
            raise MeetError(f"数据完整性错误: {str(e)}", BusinessCode.BUSINESS_ERROR.value)
        
@router.get("/meeting/participant/list", response=List[ParticipantSchemaOut])
@paginate(MyPagination)
@require_meeting_permission('view')
def list_meeting_participants(request, filters: ParticipantFilters = Query(...)):
    """获取会议参会人员列表（需要查看权限）"""
    filters = data_permission(request, filters)   
    if filters.meetingid is not None:
        queryset = MeetingParticipant.objects.filter(meeting_id=filters.meetingid).select_related('user', 'meeting')
    else:
        queryset = MeetingParticipant.objects.all().select_related('user', 'meeting')
    if filters.participantid is not None:
        queryset = queryset.filter(id=filters.participantid)
    # 应用过滤器
    if filters.is_moderator is not None:
        queryset = queryset.filter(is_moderator=filters.is_moderator)
    
    queryset = queryset.order_by('-is_moderator', '-create_datetime')
    return queryset

# ============= MeetingPhoto 会议图片相关接口 =============

def validate_image_file(image_file) -> dict:
    """验证图片文件的完整性和格式"""
    validation_errors = []
    
    file_name = image_file.name
    file_size = image_file.size
    file_ext = os.path.splitext(file_name)[1].lower()
    content_type = image_file.content_type
    
    # 允许的图片格式
    ALLOWED_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
    ALLOWED_CONTENT_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/bmp', 'image/webp']
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    
    # 验证文件扩展名
    if file_ext not in ALLOWED_EXTENSIONS:
        validation_errors.append(
            f'不支持的图片格式: {file_ext}。支持的格式: {", ".join(ALLOWED_EXTENSIONS)}'
        )
    
    # 验证Content-Type
    if content_type not in ALLOWED_CONTENT_TYPES:
        validation_errors.append(f'无效的文件类型: {content_type}')
    
    # 验证文件大小
    if file_size > MAX_FILE_SIZE:
        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
        validation_errors.append(f'文件过大: {file_size // (1024 * 1024)}MB，最大允许: {max_size_mb}MB')
    
    if validation_errors:
        raise MeetError('; '.join(validation_errors), BusinessCode.BUSINESS_ERROR.value)
    
    return {
        'name': file_name,
        'size': file_size,
        'extension': file_ext,
        'content_type': content_type
    }


class PhotoSchemaIn(ModelSchema):
    meetingid: int = Field(..., description="关联会议ID", alias="meetingid")
    fileid: int = Field(..., description="图片文件ID")
    
    class Config:
        model = MeetingPhoto
        model_exclude = ['id', 'meeting', 'file', 'create_datetime', 'update_datetime']

@require_meeting_edit_permission
@router.post("/meeting/photo/upload", response=PhotoSchemaOut)
def upload_meeting_photo(request, meetingid: int=(Query(...))):
    """
    上传会议照片（需要编辑权限）
    同一场会议，一种类型的图片最多上传5张
    
    参数：
    - meetingid: 会议ID (Query参数)
    - type: 照片类型 (FormData: 1=会议照片, 2=签到表)
    - description: 照片描述 (FormData, 可选)
    - image: 图片文件 (FormData)
    """
    try:
        meeting = get_object_or_404(Meeting, id=meetingid)

        # 1. 验证文件上传
        if 'image' not in request.FILES:
            raise MeetError('没有上传图片文件', BusinessCode.BUSINESS_ERROR.value)
        
        image_file = request.FILES['image']
        file_info = validate_image_file(image_file)
        
        # 2. 获取表单数据
        photo_type = request.POST.get('photo_type')
        description = request.POST.get('description', '')
        
        # 3. 验证照片类型
        if not photo_type:
            raise MeetError('缺少照片类型参数', BusinessCode.BUSINESS_ERROR.value)
        
        try:
            photo_type = int(photo_type)
        except ValueError:
            raise MeetError('照片类型必须是数字', BusinessCode.BUSINESS_ERROR.value)
        
        valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
        if photo_type not in valid_types:
            raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR.value)
        
        # 4. 验证数量限制
        existing_count = meeting.photos.filter(photo_type=photo_type).count()
        if existing_count >= 5:
            photo_type_name = dict(MeetingPhoto.PHOTO_TYPE_CHOICES)[photo_type]
            raise MeetError(f'每个会议的{photo_type_name}最多只能上传5张，当前已有{existing_count}张', 
                          BusinessCode.BUSINESS_ERROR.value)
        
        # 5. 使用事务确保数据一致性
        with transaction.atomic():
            # 创建File记录
            file_record = File.create_from_file(image_file, file_info['name'])
            
            # 创建MeetingPhoto记录
            photo = MeetingPhoto.objects.create(
                meeting=meeting,
                file=file_record,
                photo_type=photo_type,
                description=description
            )
            
            return photo
            
    except Exception as e:
        logger.error(f"上传会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("上传照片失败", BusinessCode.SERVER_ERROR.value)

class PhotoDeleteSchemaIn(Schema):
    photoid: int = Field(..., description="照片ID")

@router.post("/meeting/photo/delete")
@require_meeting_edit_permission
def delete_meeting_photo(request, data: PhotoDeleteSchemaIn):
    """删除会议照片（需要编辑权限）"""
    try:
        photo = get_object_or_404(MeetingPhoto, id=data.photoid)
        
        # 记录删除的文件信息用于日志
        file_name = photo.file.name if photo.file else "未知"
        photo_type = photo.get_photo_type_display()
        
        photo.delete()
        
        logger.info(f"删除会议照片成功 - 会议ID: {data.photoid}, 照片: {file_name}, 类型: {photo_type}")
        return MeetResponse(errcode=BusinessCode.OK)
        
    except Exception as e:
        logger.error(f"删除会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("删除照片失败", BusinessCode.SERVER_ERROR)

class PhotoUpdateSchemaIn(Schema):
    """照片更新Schema"""
    photoid: int = Field(..., description="照片ID", alias="photoid")
    photo_type: int = Field(..., description="照片类型")
    description: str = Field(None, description="照片描述")

@router.post("/meeting/photo/update", response=PhotoSchemaOut)
@require_meeting_edit_permission
def update_meeting_photo(request, data: PhotoUpdateSchemaIn):
    """更新会议照片信息（需要编辑权限）"""
    photo = get_object_or_404(MeetingPhoto, id=data.photoid)
    try:    
        photo_data = data.dict(exclude_unset=True)
        if 'photo_type' in photo_data:
            valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
            if photo_data['photo_type'] not in valid_types:
                raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR.value)
        for field, value in photo_data.items():
            setattr(photo, field, value)        
        photo.save()
        return photo        
    except Exception as e:
        logger.error(f"更新会议照片失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("更新照片信息失败", BusinessCode.SERVER_ERROR.value)

class PhotoFilters(MeetFilters):
    meetingid: int = Field(None, alias="meetingid", description="关联会议ID")
    photoid: int = Field(None, alias="photoid", description="照片ID")
    photo_type: int = Field(None, alias="photo_type", description="照片类型")

@router.get("/meeting/photo/list", response=List[PhotoSchemaOut])
@paginate(MyPagination)
@require_meeting_view_permission
def list_meeting_photos(request, filters: PhotoFilters = Query(...)):
    """获取会议照片列表（需要查看权限）"""
    try:
        if not filters.meetingid and not filters.photoid:
            raise MeetError("meetingid 或 photoid 至少需要提供一个", BusinessCode.BUSINESS_ERROR.value)
        
        queryset = MeetingPhoto.objects.all()
        if filters.meetingid is not None:
            queryset = queryset.filter(meeting_id=filters.meetingid)
        if filters.photoid is not None:
            queryset = queryset.filter(id=filters.photoid)        
        if filters.photo_type is not None:
            # 验证照片类型有效性
            valid_types = [choice[0] for choice in MeetingPhoto.PHOTO_TYPE_CHOICES]
            if filters.photo_type not in valid_types:
                raise MeetError("无效的照片类型", BusinessCode.BUSINESS_ERROR.value)
            queryset = queryset.filter(photo_type=filters.photo_type)
        
        photos = queryset.select_related('file').order_by('photo_type', '-create_datetime')
        return list(photos)
        
    except Exception as e:
        logger.error(f"获取会议照片列表失败: {str(e)}")
        if isinstance(e, MeetError):
            raise
        raise MeetError("获取照片列表失败", BusinessCode.SERVER_ERROR.value)