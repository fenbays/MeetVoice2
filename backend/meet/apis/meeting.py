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

router = Router()
logger = logging.getLogger(__name__)

# ============= Meeting 相关接口 =============

class MeetingFilters(MeetFilters):
    title: str = Field(None, alias="title")
    status: int = Field(None, alias="status")
    start_time__gte: datetime = Field(None, alias="start_time__gte")
    start_time__lte: datetime = Field(None, alias="start_time__lte")


class MeetingSchemaIn(ModelSchema):
    id: int = Field(None, alias="meetingid")
    class Config:
        model = Meeting
        model_fields = ['title','description','location','start_time','end_time','keywords','status']


class MeetingSchemaOut(ModelSchema):
    meetingid: int = Field(..., alias="id")
    class Config:
        model = Meeting
        model_exclude = ["id"]

class UserSchemaOut(ModelSchema):
    userid: int = Field(None, alias="id")
    class Config:
        model = Users
        model_fields = ['name', 'avatar', 'email', 'mobile']


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

    try:
        meeting = Meeting.objects.get(id=meetingid)
    except Meeting.DoesNotExist:
        raise MeetError("会议不存在", BusinessCode.INSTANCE_NOT_FOUND)
    return meeting

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
    shareid: int = Field(..., description="分享ID")
    meetingid: Optional[int] = Field(..., description="会议ID")
    userid: Optional[int] = Field(..., description="用户ID")
    is_active: bool = Field(..., description="是否激活")
    create_datetime: datetime = Field(..., description="创建时间")

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
        raise MeetError("没有有效的分享对象", BusinessCode.BUSINESS_ERROR)

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
    """获取会议分享用户列表"""
    shares = MeetingShare.objects.filter(
        meeting_id=meetingid, 
        is_active=True
    ).select_related('shared_user').order_by('-create_datetime')
    
    return [UserMeetingIdSchemaOut(
            shareid=share.id,
            userid=share.shared_user.id,
            meetingid = share.meeting_id,
            is_active=share.is_active,
            create_datetime=share.create_datetime
        ).dict() for share in shares
    ]

@router.get("/meeting/share/get_user_meetingid", response=List[UserMeetingIdSchemaOut])
@paginate(MyPagination)
def get_user_meetingid(request, userid: int=Query(...)):
    """获取成员会议ID列表"""
    request_user_info = get_user_info_from_token(request)
    request_user_id = request_user_info['id']
    if request_user_id != userid:
        raise MeetError("无权限访问", BusinessCode.PERMISSION_DENIED)
    shares = MeetingShare.objects.filter(
        shared_user_id=userid, 
        is_active=True
    ).select_related('meeting').order_by('-create_datetime')
    return [
        UserMeetingIdSchemaOut(
            shareid=share.id,
            meetingid=share.meeting.id,
            userid=share.shared_user.id,
            is_active=share.is_active,
            create_datetime=share.create_datetime
        ).dict()
        for share in shares
    ]


# ============= Recording 相关接口 =============

class RecordingFilters(MeetFilters):
    meeting_id: int = Field(None, alias="meeting_id")
    process_status: int = Field(None, alias="process_status")
    uploader_id: int = Field(None, alias="uploader_id")


class RecordingSchemaIn(ModelSchema):
    meeting_id: int = Field(..., description="关联会议ID")
    file_id: int = Field(..., description="录音文件ID")
    
    class Config:
        model = Recording
        model_exclude = ['id', 'meeting', 'file', 'uploader', 'create_datetime', 'update_datetime']


class RecordingSchemaOut(ModelSchema):
    class Config:
        model = Recording
        model_fields = "__all__"

@router.post("/recording/create", response=RecordingSchemaOut)
def create_recording(request, data: RecordingSchemaIn):
    """
    上传音频文件接口 - 使用传统HTTP API + 后台任务
    POST /api/transcription/upload/
    """
    try:
        # 获取会议ID
        meeting_id = data.meeting_id
        if not meeting_id:
            raise MeetError('会议ID必填', BusinessCode.BUSINESS_ERROR)
        
        # 验证会议是否存在
        try:
            meeting = Meeting.objects.get(id=meeting_id)
        except Meeting.DoesNotExist:
            raise MeetError('会议不存在', BusinessCode.INSTANCE_NOT_FOUND)
        
        # 检查是否有上传的文件
        if 'audio_file' not in request.FILES:
            raise MeetError('没有上传音频文件', BusinessCode.BUSINESS_ERROR)
        
        audio_file = request.FILES['audio_file']
        
        # 验证文件类型
        import os
        allowed_extensions = ['.mp3', '.wav', '.webm', '.ogg', '.m4a', '.flac']
        file_ext = os.path.splitext(audio_file.name)[1].lower()
        if file_ext not in allowed_extensions:
            raise MeetError(f'不支持的文件格式。支持的格式: {", ".join(allowed_extensions)}', BusinessCode.BUSINESS_ERROR)
        
        # 验证文件大小 (限制为100MB)
        max_size = 1000 * 1024 * 1024  # 100MB
        if audio_file.size > max_size:
            raise MeetError('文件太大，最大支持1000MB', BusinessCode.BUSINESS_ERROR)
        
        # 1. 创建File记录
        file_record = File.create_from_file(audio_file, audio_file.name)
        
        # 2. 创建Recording记录
        recording = Recording.objects.create(
            meeting=meeting,
            file=file_record,
            uploader=request.user if request.user.is_authenticated else None,
            duration=None,  # 将在处理时计算
            keywords=data.keywords,  # 可选的额外关键词
            process_status=0  # 未处理
        )
        
        # 3. 启动后台处理任务
        from .tasks import process_uploaded_audio
        task = process_uploaded_audio.delay(recording.id)
        
        logger.info(f'音频文件上传成功，录音ID: {recording.id}, 任务ID: {task.id}')
        
        # 4. 返回录音信息
        return MeetResponse(data={
            'recording_id': recording.id,
            'task_id': task.id,
            'meeting_id': meeting_id,
            'meeting_title': meeting.title,
            'file_name': audio_file.name,
            'file_size': audio_file.size,
            'status': 'processing',
            'status_url': f'/api/transcription/status/{recording.id}/'
        }, errcode=BusinessCode.OK)
        
    except Exception as e:
        import traceback
        logger.error(f'音频文件上传失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError(f'上传失败', BusinessCode.SERVER_ERROR)

@router.delete("/recording/delete")
def delete_recording(request, recordingid: int=Query(...)):
    """删除录音文件"""
    delete(recordingid, Recording)
    return MeetResponse(errcode=BusinessCode.OK)


@router.put("/recording/update", response=RecordingSchemaOut)
def update_recording(request, data: RecordingSchemaIn):
    """更新录音信息"""
    recording_data = data.dict()
    # 处理外键关系
    if 'meeting_id' in recording_data:
        recording_data['meeting_id'] = recording_data.pop('meeting_id')
    if 'file_id' in recording_data:
        recording_data['file_id'] = recording_data.pop('file_id')
    
    recording = update(request, data.id, recording_data, Recording)
    return recording


@router.get("/recording/list", response=List[RecordingSchemaOut])
@paginate(MyPagination)
def list_recording(request, filters: RecordingFilters = Query(...)):
    """分页获取录音列表"""
    qs = retrieve(request, Recording, filters)
    return qs


@router.get("/recording/get", response=RecordingSchemaOut)
def get_recording(request, recordingid: int=Query(...)):
    """获取录音详情"""
    recording = get_object_or_404(Recording, id=recordingid)
    return recording


@router.get("/meeting/{meeting_id}/recordings/list", response=List[RecordingSchemaOut])
def list_meeting_recordings(request, meeting_id: int):
    """获取指定会议的所有录音"""
    recordings = Recording.objects.filter(meeting_id=meeting_id)
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


@router.post("/summary", response=SummarySchemaOut)
def create_summary(request, data: SummarySchemaIn):
    """创建会议纲要"""
    summary_data = data.dict()
    summary_data['meeting_id'] = summary_data.pop('meeting_id')
    
    summary = create(request, summary_data, MeetingSummary)
    return summary


@router.delete("/summary/{summary_id}")
def delete_summary(request, summary_id: int):
    """删除会议纲要"""
    delete(summary_id, MeetingSummary)
    return {"success": True}


@router.put("/summary/{summary_id}", response=SummarySchemaOut)
def update_summary(request, summary_id: int, data: SummarySchemaIn):
    """更新会议纲要"""
    summary_data = data.dict()
    if 'meeting_id' in summary_data:
        summary_data['meeting_id'] = summary_data.pop('meeting_id')
    
    summary = update(request, summary_id, summary_data, MeetingSummary)
    return summary


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
    
    class Config:
        model = MeetingPhoto
        model_fields = "__all__"
    
    @staticmethod
    def resolve_file_url(obj):
        return obj.file.get_absolute_url() if obj.file else None
    
    @staticmethod
    def resolve_file_name(obj):
        return obj.file.name if obj.file else None


@require_meeting_edit_permission
@router.post("/meeting/{meeting_id}/photo", response=PhotoSchemaOut)
def upload_meeting_photo(request, meeting_id: int, data: PhotoSchemaIn):
    """上传会议照片（需要编辑权限）"""
    photo_data = data.dict()
    photo_data['meeting_id'] = meeting_id
    
    # 验证文件存在
    file = get_object_or_404(File, id=photo_data['file_id'])
    photo_data['file_id'] = photo_data.pop('file_id')
    
    photo = create(request, photo_data, MeetingPhoto)
    return photo


@require_meeting_edit_permission
@router.delete("/meeting/{meeting_id}/photo/{photo_id}")
def delete_meeting_photo(request, meeting_id: int, photo_id: int):
    """删除会议照片（需要编辑权限）"""
    photo = get_object_or_404(MeetingPhoto, 
                             id=photo_id, 
                             meeting_id=meeting_id)
    photo.delete()
    return {"success": True}


@require_meeting_edit_permission
@router.put("/meeting/{meeting_id}/photo/{photo_id}", response=PhotoSchemaOut)
def update_meeting_photo(request, meeting_id: int, photo_id: int, data: PhotoSchemaIn):
    """更新会议照片信息（需要编辑权限）"""
    photo_data = data.dict()
    
    # 处理文件关联
    if 'file_id' in photo_data:
        file = get_object_or_404(File, id=photo_data['file_id'])
        photo_data['file_id'] = photo_data.pop('file_id')
    
    photo = update(request, photo_id, photo_data, MeetingPhoto)
    return photo


@require_meeting_view_permission
@router.get("/meeting/{meeting_id}/photos", response=List[PhotoSchemaOut])
def list_meeting_photos(request, meeting_id: int, photo_type: int = None):
    """获取会议照片列表（需要查看权限）"""
    queryset = MeetingPhoto.objects.filter(meeting_id=meeting_id)
    
    if photo_type is not None:
        queryset = queryset.filter(photo_type=photo_type)
    
    photos = queryset.select_related('file').order_by('photo_type', '-create_datetime')
    return list(photos)


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


# ========== 录音相关权限控制 ==========

@require_meeting_owner  # 只有所属人可以上传录音
@router.post("/meeting/{meeting_id}/recording", response=RecordingSchemaOut)
def upload_meeting_recording(request, meeting_id: int, data: RecordingSchemaIn):
    """上传会议录音（仅所属人）"""
    data_dict = data.dict()
    data_dict['meeting_id'] = meeting_id
    recording = create(request, data_dict, Recording)
    return recording


@require_meeting_view_permission  # 有查看权限的都可以下载
@router.get("/meeting/{meeting_id}/recordings/download/{recording_id}")
def download_meeting_recording(request, meeting_id: int, recording_id: int):
    """下载会议录音（所属人和被分享者都可以）"""
    # 这里实现文件下载逻辑
    pass
