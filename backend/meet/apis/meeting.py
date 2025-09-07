from typing import List, Optional
from datetime import datetime
import traceback

from django.shortcuts import get_object_or_404
from django.db import transaction
from ninja import Field, ModelSchema, Query, Router, Schema
from ninja.pagination import paginate
from utils.usual import get_user_info_from_token
from utils.anti_duplicate import anti_duplicate
from system.models import File, Users
from meet.models import Meeting, Recording, Speaker, TranscriptSegment, MeetingShare, MeetingSummary, MeetingParticipant, MeetingPhoto
from utils.meet_crud import create, delete, retrieve, update
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import MeetResponse, MeetError, BusinessCode
from meet.permissions import (
    require_meeting_edit_permission, 
    require_meeting_view_permission,
    require_meeting_owner,
    get_user_meetings_queryset
)
from utils.meet_response import MeetResponse
import logging
import os

router = Router()
logger = logging.getLogger(__name__)

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
        model_fields = ['title','description','location','start_time','end_time','keywords','status']


class MeetingSchemaOut(ModelSchema):
    meetingid: int = Field(..., alias="id")
    class Config:
        model = Meeting
        model_exclude = ["id"]

class UserSchemaOut(ModelSchema):
    userid: int = Field(..., alias="id")
    class Config:
        model = Users
        model_fields = ['id','name', 'avatar', 'email', 'mobile']


@router.post("/meeting/create", response=MeetingSchemaOut)
@anti_duplicate(expire_time=10)
def create_meeting(request, data: MeetingSchemaIn):
    """创建新会议"""
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(Users, id=request_user['id'], is_active=True)
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

    # try:
    #     meeting = Meeting.objects.get(id=meetingid)
    # except Meeting.DoesNotExist:
    #     raise MeetError("会议不存在", BusinessCode.INSTANCE_NOT_FOUND.value)
    # return meeting

@require_meeting_owner
@router.post("/meeting/update", response=MeetingSchemaOut)
def update_meeting(request, data: MeetingSchemaIn):
    """更新会议信息（需要编辑权限）"""
    meeting = update(request, data.id, data, Meeting)
    return meeting

@router.get("/meeting/list", response=List[MeetingSchemaOut])
@paginate(MyPagination)
def list_meeting(request, filters: MeetingFilters = Query(...)):
    """获取用户可访问的会议列表"""
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(Users, id=request_user['id'], is_active=True)
    # 不使用通用retrieve，直接用权限过滤
    qs = get_user_meetings_queryset(user_obj)
    # 这里可以添加其他过滤条件
    return qs

@require_meeting_edit_permission
@router.get("/meeting/delete")
def delete_meeting(request, meetingid: int=Query(...)):
    """删除会议（需要编辑权限）"""
    delete(meetingid, Meeting)
    return MeetResponse(errcode=BusinessCode.OK)

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
    existing_users = Users.objects.filter(
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
                    user = Users.objects.get(id=user_id)
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
    
    req_user_obj = get_object_or_404(Users, id=request_user_id)
    
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


# ============= Recording 相关接口 =============

class RecordingFilters(MeetFilters):
    meetingid: int = Field(None, alias="meetingid")
    process_status: int = Field(None, alias="process_status")
    uploader_id: int = Field(None, alias="uploader_id")


class RecordingSchemaIn(ModelSchema):
    meetingid: int = Field(..., description="会议ID")
    keywords: str = Field(None, description="录音关键词")
    
    class Config:
        model = Recording
        model_exclude = ['id', 'meeting', 'file', 'uploader', 'create_datetime', 'update_datetime']


class RecordingSchemaOut(ModelSchema):
    class Config:
        model = Recording
        model_fields = "__all__"

@router.post("/recording/create", response=RecordingSchemaOut)
@require_meeting_owner  # 添加权限装饰器，确保只有会议所有者可以上传
def create_recording(request, meetingid: int=Query(...)):
    """
    上传音频文件接口
    只有会议所有者可以上传音频文件
    """
    try:
        # 验证会议是否存在
        try:
            meeting = Meeting.objects.get(id=meetingid)
        except Meeting.DoesNotExist:
            raise MeetError('会议不存在', BusinessCode.INSTANCE_NOT_FOUND.value)
        
        # 检查是否有上传的文件
        if 'audio' not in request.FILES:
            raise MeetError('没有上传音频文件', BusinessCode.BUSINESS_ERROR.value)
        
        audio_file = request.FILES['audio']
        
        # 验证文件类型
        allowed_extensions = ['.mp3', '.wav', '.webm', '.ogg', '.m4a', '.flac']
        file_ext = os.path.splitext(audio_file.name)[1].lower()
        if file_ext not in allowed_extensions:
            raise MeetError(f'不支持的文件格式。支持的格式: {", ".join(allowed_extensions)}', BusinessCode.BUSINESS_ERROR.value)
        
        # 验证文件大小 (限制为1000MB)
        max_size = 1000 * 1024 * 1024  # 1000MB
        if audio_file.size > max_size:
            raise MeetError('文件太大，最大支持1000MB', BusinessCode.BUSINESS_ERROR.value)
        
        # 验证文件内容类型
        content_type = audio_file.content_type
        logger.info(f'audio_file content_type: {content_type}')
        allowed_content_types = [
            'audio/mpeg', 'audio/wave', 'audio/webm', 
            'audio/ogg', 'audio/mp4', 'audio/x-flac'
        ]
        if content_type not in allowed_content_types:
            raise MeetError('无效的音频文件类型', BusinessCode.BUSINESS_ERROR.value)
            
        # 验证文件名长度
        if len(audio_file.name) > 255:
            raise MeetError('文件名过长，请修改后重试', BusinessCode.BUSINESS_ERROR.value)
        
        request_user = get_user_info_from_token(request)
        # 使用事务确保数据一致性
        with transaction.atomic():
            # 1. 创建File记录
            file_record = File.create_from_file(audio_file, audio_file.name)
            
            # 2. 创建Recording记录
            recording = Recording.objects.create(
                meeting=meeting,
                file=file_record,
                uploader_id=request_user['id'],  # 明确设置上传者ID
                duration=None,  # 将在处理时计算
                process_status=0  # 未处理
            )
            
            # 3. 启动后台处理任务
            from meet.tasks import process_uploaded_audio
            task = process_uploaded_audio.delay(recording.id)
            
            logger.info(f'音频文件上传成功，录音ID: {recording.id}, 任务ID: {task.id}, 上传者ID: {request_user["id"]}')
            
            # 4. 返回录音信息
            return MeetResponse(data={
                'recording_id': recording.id,
                'task_id': task.id,
                'meeting_id': meetingid,
                'meeting_title': meeting.title,
                'file_name': audio_file.name,
                'file_size': audio_file.size,
                'uploader_id': request_user["id"],
                'status': 'processing',
                'status_url': f'/api/transcription/status/{recording.id}/'
            }, errcode=BusinessCode.OK)
            
    except MeetError as e:
        # 业务逻辑错误，直接抛出
        raise e
    except Exception as e:
        # 其他未预期的错误
        logger.error(f'音频文件上传失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError('服务器错误，上传失败', BusinessCode.SERVER_ERROR.value)

@router.delete("/recording/delete")
@require_meeting_owner  # 只有会议所有者可以删除录音
def delete_recording(request, recordingid: int=Query(...)):
    """删除录音文件"""
    # 获取录音对象
    recording = get_object_or_404(Recording, id=recordingid)
    
    # 删除关联的文件
    if recording.file:
        recording.file.delete()  # 这会同时删除物理文件
        
    delete(recordingid, Recording)
    return MeetResponse(errcode=BusinessCode.OK)

@router.get("/meeting/recording/list", response=List[RecordingSchemaOut])
@require_meeting_view_permission
@paginate(MyPagination)
def list_recording(request, filters: RecordingFilters = Query(...)):
    """分页获取会议录音列表"""
    request_user = get_user_info_from_token(request)
    # 获取用户可访问的会议ID列表
    accessible_meetings = get_user_meetings_queryset(
        get_object_or_404(Users, id=request_user['id'])
    ).values_list('id', flat=True)
    
    # 基于filters获取查询集
    qs = retrieve(request, Recording, filters)
    
    # 过滤只返回用户有权限访问的会议的录音
    qs = qs.filter(meeting_id__in=accessible_meetings)
    
    return qs

@router.get("/recording/get", response=RecordingSchemaOut)
@require_meeting_view_permission  # 需要会议查看权限
def get_recording(request, recordingid: int=Query(...)):
    """获取录音详情"""
    recording = get_object_or_404(Recording, id=recordingid)
    
    # 验证用户是否有权限访问该录音所属的会议
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(Users, id=request_user['id'])
    
    accessible_meetings = get_user_meetings_queryset(user_obj).values_list('id', flat=True)
    if recording.meeting_id not in accessible_meetings:
        raise MeetError('无权访问该录音', BusinessCode.PERMISSION_DENIED)
    
    return recording

@router.get("/meeting/{meeting_id}/recordings/list", response=List[RecordingSchemaOut])
@require_meeting_view_permission  # 需要会议查看权限
def list_meeting_recordings(request, meeting_id: int):
    """获取指定会议的所有录音"""
    # 验证会议是否存在
    meeting = get_object_or_404(Meeting, id=meeting_id)
    
    # 获取该会议的所有录音并按创建时间倒序排序
    recordings = Recording.objects.filter(meeting_id=meeting_id).order_by('-create_datetime')
    
    return list(recordings)


# ============= Speaker 相关接口 =============

class SpeakerFilters(MeetFilters):
    recording_id: int = Field(None, alias="recording_id")
    speaker_id: str = Field(None, alias="speaker_id")


class SpeakerSchemaIn(ModelSchema):
    recording_id: int = Field(..., description="关联录音ID")
    
    class Config:
        model = Speaker
        model_exclude = ['id', 'recording', 'create_datetime', 'update_datetime']


class SpeakerSchemaOut(ModelSchema):
    class Config:
        model = Speaker
        model_fields = "__all__"


@router.post("/speaker/create", response=SpeakerSchemaOut)
def create_speaker(request, data: SpeakerSchemaIn):
    """创建说话人"""
    speaker_data = data.dict()
    speaker_data['recording_id'] = speaker_data.pop('recording_id')
    
    speaker = create(request, speaker_data, Speaker)
    return speaker


@router.delete("/speaker/delete")
def delete_speaker(request, speakerid: int = Query(...)):
    """删除说话人"""
    delete(speakerid, Speaker)
    return {"success": True}


@router.put("/speaker/update", response=SpeakerSchemaOut)
def update_speaker(request, data: SpeakerSchemaIn):
    """更新说话人信息"""
    speaker_data = data.dict()
    if 'recording_id' in speaker_data:
        speaker_data['recording_id'] = speaker_data.pop('recording_id')
    
    speaker = update(request, data.id, speaker_data, Speaker)
    return speaker


@router.get("/speaker/list", response=List[SpeakerSchemaOut])
@paginate(MyPagination)
def list_speaker(request, filters: SpeakerFilters = Query(...)):
    """分页获取说话人列表"""
    qs = retrieve(request, Speaker, filters)
    return qs


@router.get("/speaker/get", response=SpeakerSchemaOut)
def get_speaker(request, speakerid: int = Query(...)):
    """获取说话人详情"""
    speaker = get_object_or_404(Speaker, id=speakerid)
    return speaker


@router.get("/recording/{recording_id}/speakers/list", response=List[SpeakerSchemaOut])
def list_recording_speakers(request, recording_id: int):
    """获取指定录音的所有说话人"""
    speakers = Speaker.objects.filter(recording_id=recording_id)
    return list(speakers)


# ============= TranscriptSegment 相关接口 =============

class TranscriptFilters(MeetFilters):
    recording_id: int = Field(None, alias="recording_id")
    speaker_id: int = Field(None, alias="speaker_id")


class TranscriptSchemaIn(ModelSchema):
    recording_id: int = Field(..., description="关联录音ID")
    speaker_id: int = Field(..., description="说话人ID")
    
    class Config:
        model = TranscriptSegment
        model_exclude = ['id', 'recording', 'speaker', 'create_datetime', 'update_datetime']


class TranscriptSchemaOut(ModelSchema):
    class Config:
        model = TranscriptSegment
        model_fields = "__all__"


@router.post("/transcript", response=TranscriptSchemaOut)
def create_transcript(request, data: TranscriptSchemaIn):
    """创建转录片段"""
    transcript_data = data.dict()
    transcript_data['recording_id'] = transcript_data.pop('recording_id')
    transcript_data['speaker_id'] = transcript_data.pop('speaker_id')
    
    transcript = create(request, transcript_data, TranscriptSegment)
    return transcript


@router.delete("/transcript/{transcript_id}")
def delete_transcript(request, transcript_id: int):
    """删除转录片段"""
    delete(transcript_id, TranscriptSegment)
    return {"success": True}


@router.put("/transcript/{transcript_id}", response=TranscriptSchemaOut)
def update_transcript(request, transcript_id: int, data: TranscriptSchemaIn):
    """更新转录片段"""
    transcript_data = data.dict()
    if 'recording_id' in transcript_data:
        transcript_data['recording_id'] = transcript_data.pop('recording_id')
    if 'speaker_id' in transcript_data:
        transcript_data['speaker_id'] = transcript_data.pop('speaker_id')
    
    transcript = update(request, transcript_id, transcript_data, TranscriptSegment)
    return transcript


@router.get("/transcript", response=List[TranscriptSchemaOut])
@paginate(MyPagination)
def list_transcript(request, filters: TranscriptFilters = Query(...)):
    """分页获取转录片段列表"""
    qs = retrieve(request, TranscriptSegment, filters)
    return qs


@router.get("/transcript/{transcript_id}", response=TranscriptSchemaOut)
def get_transcript(request, transcript_id: int):
    """获取转录片段详情"""
    transcript = get_object_or_404(TranscriptSegment, id=transcript_id)
    return transcript


@router.get("/recording/{recording_id}/transcripts", response=List[TranscriptSchemaOut])
def list_recording_transcripts(request, recording_id: int):
    """获取指定录音的所有转录片段"""
    transcripts = TranscriptSegment.objects.filter(recording_id=recording_id).order_by('start_time')
    return list(transcripts)


# ============= MeetingSummary 相关接口 =============

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
@paginate(MyPagination)
def list_summary(request, filters: SummaryFilters = Query(...)):
    """分页获取会议纲要列表"""
    qs = retrieve(request, MeetingSummary, filters)
    return qs


@router.get("/summary/{summary_id}", response=SummarySchemaOut)
def get_summary(request, summary_id: int):
    """获取会议纲要详情"""
    summary = get_object_or_404(MeetingSummary, id=summary_id)
    return summary


@router.get("/meeting/{meeting_id}/summary", response=SummarySchemaOut)
def get_meeting_summary(request, meeting_id: int):
    """获取指定会议的纲要"""
    summary = get_object_or_404(MeetingSummary, meeting_id=meeting_id)
    return summary


# ============= MeetingParticipant 相关接口 =============

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
        user = get_object_or_404(Users, id=participant_data['user_id'])
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
            user = get_object_or_404(Users, id=participant_data['user_id'])
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


# ============= MeetingPhoto 相关接口 =============

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


# ============= 照片类型枚举接口 =============

@router.get("/photo-types")
def get_photo_types(request):
    """获取照片类型枚举列表"""
    return [
        {"value": choice[0], "label": choice[1]} 
        for choice in MeetingPhoto.PHOTO_TYPE_CHOICES
    ]


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
    transcripts: List[TranscriptSchemaOut]


@router.get("/recording/{recording_id}/detail")
def get_recording_detail(request, recording_id: int):
    """获取录音完整详情（包含说话人和转录）"""
    recording = get_object_or_404(Recording, id=recording_id)
    speakers = list(Speaker.objects.filter(recording_id=recording_id))
    transcripts = list(TranscriptSegment.objects.filter(recording_id=recording_id).order_by('start_time'))
    
    return MeetResponse(data={
        "recording": recording,
        "speakers": speakers,
        "transcripts": transcripts
    })
