from typing import List
import traceback

from django.shortcuts import get_object_or_404
from django.db import transaction
from django.contrib.auth import get_user_model
from ninja import Field, ModelSchema, Query, Router
from pydantic import computed_field
from utils.usual import get_user_info_from_token
from system.models import File
from meet.models import Meeting, Recording, Speaker, TranscriptSegment
from utils.meet_crud import delete, retrieve
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import MeetResponse, MeetError, BusinessCode
from meet.permissions import (
    require_meeting_view_permission,
    require_meeting_owner,
    get_user_meetings_queryset
)
from utils.meet_response import MeetResponse
import logging
import os
User = get_user_model()

router = Router()
logger = logging.getLogger(__name__)

class AudioFileConfig:
    """音频文件配置常量"""
    ALLOWED_EXTENSIONS = ['.mp3', '.wav', '.m4a', '.mp4']
    ALLOWED_CONTENT_TYPES = [
        'audio/mpeg',
        'audio/wave',
        'audio/mp4',
        'audio/x-wav',  # 添加更多支持的MIME类型
        'audio/mp4a-latm'
    ]
    MAX_FILE_SIZE = 1000 * 1024 * 1024  # 1000MB
    MAX_FILENAME_LENGTH = 255

def validate_audio_file(audio_file) -> dict:
    """
    验证音频文件的完整性和格式

    Args:
        audio_file: Django上传的文件对象

    Returns:
        dict: 包含验证结果的字典

    Raises:
        MeetError: 当验证失败时抛出
    """
    validation_errors = []

    # 获取文件基本信息（只读取一次，提高性能）
    file_name = audio_file.name
    file_size = audio_file.size
    file_ext = os.path.splitext(file_name)[1].lower()
    content_type = audio_file.content_type

    # 验证文件扩展名
    if file_ext not in AudioFileConfig.ALLOWED_EXTENSIONS:
        validation_errors.append(
            f'不支持的文件格式: {file_ext}。支持的格式: {", ".join(AudioFileConfig.ALLOWED_EXTENSIONS)}'
        )

    # 验证文件大小
    if file_size > AudioFileConfig.MAX_FILE_SIZE:
        max_size_mb = AudioFileConfig.MAX_FILE_SIZE // (1024 * 1024)
        validation_errors.append(f'文件太大 ({file_size // (1024 * 1024)}MB)，最大支持 {max_size_mb}MB')

    # 验证文件内容类型
    if content_type not in AudioFileConfig.ALLOWED_CONTENT_TYPES:
        logger.warning(f'未识别的音频文件类型: {content_type}, 文件名: {file_name}')
        # 对于内容类型验证，我们给出警告但不阻止上传，因为有些客户端可能发送不准确的MIME类型

    # 验证文件名长度
    if len(file_name) > AudioFileConfig.MAX_FILENAME_LENGTH:
        validation_errors.append(f'文件名过长 ({len(file_name)} 字符)，最大支持 {AudioFileConfig.MAX_FILENAME_LENGTH} 字符')

    # 验证文件名是否包含危险字符
    dangerous_chars = ['..', '/', '\\', ':', '*', '?', '"', '<', '>', '|']
    if any(char in file_name for char in dangerous_chars):
        validation_errors.append('文件名包含非法字符')

    # 如果有验证错误，抛出异常
    if validation_errors:
        error_message = '; '.join(validation_errors)
        raise MeetError(error_message, BusinessCode.BUSINESS_ERROR.value)

    # 记录验证成功的信息
    logger.info(f'音频文件验证通过: {file_name}, 大小: {file_size} bytes, 类型: {content_type}')

    return {
        'name': file_name,
        'size': file_size,
        'extension': file_ext,
        'content_type': content_type
    }

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
        model_exclude = ['file', 'uploader']
        
    @computed_field
    @property
    def fileid(self) -> int:
        # 在Django Ninja中，self应该就是模型实例
        if hasattr(self, 'file') and self.file:
            return self.file.id
        return None
    
    @computed_field
    @property
    def uploaderid(self) -> int:
        if hasattr(self, 'uploader') and self.uploader:
            return self.uploader.id
        return None
    
    @computed_field
    @property
    def process_status_display(self) -> str:
        if hasattr(self, 'get_process_status_display'):
            return self.get_process_status_display()
        return ""


@router.post("/recording/create", response=RecordingSchemaOut)
@require_meeting_owner
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

        # 使用新的验证函数
        file_info = validate_audio_file(audio_file)

        request_user = get_user_info_from_token(request)

        # 使用事务确保数据一致性
        with transaction.atomic():
            # 1. 创建File记录
            file_record = File.create_from_file(audio_file, file_info['name'])

            # 2. 创建Recording记录
            recording = Recording.objects.create(
                meeting=meeting,
                file=file_record,
                uploader_id=request_user['id'],
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
                'file_name': file_info['name'],
                'file_size': file_info['size'],
                'uploader_id': request_user["id"],
                'status': 'processing',
                'status_url': f'/api/transcription/status/{recording.id}/'
            }, errcode=BusinessCode.OK)

    except MeetError as e:
        raise e
    except Exception as e:
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

# @router.get("/meeting/recording/list", response=List[RecordingSchemaOut])
# @require_meeting_view_permission
# @paginate(MyPagination)
# def list_recording(request, filters: RecordingFilters = Query(...)):
#     """分页获取会议录音列表"""
#     request_user = get_user_info_from_token(request)
#     # 获取用户可访问的会议ID列表
#     accessible_meetings = get_user_meetings_queryset(
#         get_object_or_404(User, id=request_user['id'])
#     ).values_list('id', flat=True)

#     # 基于filters获取查询集
#     qs = retrieve(request, Recording, filters)

#     # 过滤只返回用户有权限访问的会议的录音
#     qs = qs.filter(meeting_id__in=accessible_meetings)

#     return qs

@router.get("/recording/get", response=RecordingSchemaOut)
@require_meeting_view_permission  # 需要会议查看权限
def get_recording(request, recordingid: int=Query(...)):
    """获取录音详情"""
    recording = get_object_or_404(Recording, id=recordingid)

    # 验证用户是否有权限访问该录音所属的会议
    request_user = get_user_info_from_token(request)
    user_obj = get_object_or_404(User, id=request_user['id'])

    accessible_meetings = get_user_meetings_queryset(user_obj).values_list('id', flat=True)
    if recording.meeting_id not in accessible_meetings:
        raise MeetError('无权访问该录音', BusinessCode.PERMISSION_DENIED)

    return recording

@router.get("/meeting/recording/list", response=List[RecordingSchemaOut])
@require_meeting_view_permission  # 需要会议查看权限
def list_meeting_recordings(request, meetingid: int=Query(...)):
    """获取指定会议的所有录音"""
    meeting = get_object_or_404(Meeting, id=meetingid)
    recordings = Recording.objects.filter(meeting=meeting).order_by('-create_datetime')
    return list(recordings)


@router.get("/recording/transcription/status")
def get_processing_status(request, recordingid: int=Query(...)):
    """
    获取录音处理的状态信息接口
    
    接口路径: GET /recording/transcription/status
    参数: recordingid (int) - 录音记录ID，必填
    
    功能说明:
    该接口用于获取指定录音文件的处理状态和详细信息，支持以下场景：
    1. 未处理状态(0): 返回基础录音信息
    2. 处理中状态(1): 返回处理进度提示信息
    3. 已完成状态(2): 返回完整的转录结果，包括说话人信息、分段文本、统计信息等
    4. 处理失败状态(3): 返回错误信息和重试建议
    
    返回数据结构:
    - 基础信息: 录音ID、会议ID、处理状态、文件名、时长、上传时间、上传人、关键词
    - 处理完成时: 说话人信息、转录文本、分段信息、处理后音频URL、处理完成时间
    - 处理失败时: 错误信息和重试URL
    - 处理中时: 进度提示信息
    
    权限验证: 用户必须对录音所属的会议有查看权限
    """
    try:
        # 获取录音记录，包含关联的会议信息
        recording = Recording.objects.select_related('meeting', 'file').get(id=recordingid)
        
        # 验证用户权限（通过装饰器自动处理）
        request_user = get_user_info_from_token(request)
        user_obj = get_object_or_404(User, id=request_user['id'])
        
        # 检查用户是否有权限访问该录音所属的会议
        if not recording.meeting.user_can_view(user_obj):
            raise MeetError('无权访问该录音', BusinessCode.PERMISSION_DENIED)
        
        # 构建基础响应数据
        response_data = {
            'recording_id': recordingid,
            'meeting_id': recording.meeting.id,
            'meeting_title': recording.meeting.title,
            'process_status': recording.process_status,
            'status_text': recording.get_process_status_display(),
            'file_name': recording.file.name if recording.file else None,
            'file_size': recording.file.size if recording.file else None,
            'duration': recording.duration,
            'upload_time': recording.create_datetime,
            'uploader_name': recording.uploader.name if recording.uploader else None,
            'keywords': recording.get_keywords_string(),
        }
        
        # 根据处理状态添加详细信息
        if recording.process_status == 2:  # 已完成
            # 获取说话人信息
            speakers = recording.speakers.all().order_by('speaker_id')
            
            # 获取转录片段，优化查询性能
            transcripts = recording.transcripts.select_related('speaker').order_by('start_time')
            
            # 构建说话人列表
            speakers_list = []
            for speaker in speakers:
                speakers_list.append({
                    'speaker_id': speaker.speaker_id,
                    'name': speaker.name or speaker.speaker_id,
                    'title': speaker.title,
                    'department': speaker.department,
                    'company': speaker.company,
                    'segments_count': speaker.segments.count(),
                    'total_speech_time': _calculate_speaker_total_time(speaker)
                })
            
            # 构建转录文本（完整版本）
            full_transcription = '\n'.join([
                f"[{segment.start_time.strftime('%H:%M:%S')}] {segment.speaker.speaker_id}: {segment.text}"
                for segment in transcripts
            ])
            
            # 构建分段列表
            segments_list = []
            for segment in transcripts:
                segments_list.append({
                    'segment_id': segment.id,
                    'speaker_id': segment.speaker.speaker_id,
                    'speaker_name': segment.speaker.name or segment.speaker.speaker_id,
                    'start_time': segment.start_time.strftime('%H:%M:%S.%f')[:-3],
                    'end_time': segment.end_time.strftime('%H:%M:%S.%f')[:-3],
                    'duration_seconds': _calculate_segment_duration(segment),
                    'text': segment.text,
                    'confidence': segment.confidence,
                    'word_count': _calculate_word_count(segment.text)
                })
            
            # 计算统计信息
            total_segments = transcripts.count()
            total_speakers = speakers.count()
            total_words = sum(_calculate_word_count(segment.text) for segment in transcripts if segment.text)
            average_confidence = sum(seg.confidence for seg in transcripts if seg.confidence) / total_segments if total_segments > 0 else 0
            
            response_data.update({
                'speakers_count': total_speakers,
                'speakers': speakers_list,
                'transcription': full_transcription,
                'segments': segments_list,
                'statistics': {
                    'total_segments': total_segments,
                    'total_speakers': total_speakers,
                    'total_words': total_words,
                    'average_confidence': round(average_confidence, 3),
                    'processing_completed_at': recording.update_datetime
                },
                'processed_audio_url': f'/media/processed/{recording.id}/processed_audio.wav',
                'download_urls': {
                    'transcription_txt': f'/api/transcription/download/{recording.id}/transcription.txt',
                    'transcription_json': f'/api/transcription/download/{recording.id}/transcription.json',
                    'processed_audio': f'/media/processed/{recording.id}/processed_audio.wav'
                }
            })
            
        elif recording.process_status == 3:  # 处理失败
            response_data.update({
                'error': '处理失败，请重试或联系管理员',
                'error_details': '音频文件处理过程中出现错误，请检查文件格式或重新上传',
                'retry_url': f'/api/transcription/retry/{recording.id}/'
            })
            
        elif recording.process_status == 1:  # 处理中
            # 可以添加处理进度信息（如果有的话）
            response_data.update({
                'progress_info': '音频文件正在处理中，请稍候...',
                'estimated_completion': '处理时间取决于文件大小，通常需要几分钟到几十分钟'
            })
        
        return MeetResponse(data=response_data, errcode=BusinessCode.OK)
        
    except Recording.DoesNotExist:
        raise MeetError('录音记录不存在', BusinessCode.INSTANCE_NOT_FOUND)
    except MeetError:
        raise
    except Exception as e:
        logger.error(f'获取处理状态失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError('服务器错误，获取状态失败', BusinessCode.SERVER_ERROR)

def _calculate_speaker_total_time(speaker: Speaker) -> int:
    """计算说话人总发言时间（秒）"""
    from django.db.models import Sum, F
    from datetime import timedelta
    
    total_seconds = speaker.segments.aggregate(
        total=Sum(F('end_time') - F('start_time'))
    )['total']
    
    if total_seconds:
        return total_seconds.total_seconds()
    return 0

def _calculate_segment_duration(segment: TranscriptSegment) -> float:
    """计算片段时长（秒）"""
    from datetime import datetime
    
    # 将时间转换为datetime对象进行计算
    start = datetime.combine(datetime.today(), segment.start_time)
    end = datetime.combine(datetime.today(), segment.end_time)
    
    # 如果结束时间小于开始时间，说明跨越了午夜
    if end < start:
        end = end.replace(day=end.day + 1)
    
    duration = end - start
    return duration.total_seconds()

def _calculate_word_count(text: str) -> int:
    """
    计算中英文混合文本的词数
    中文：每个字符算一个词
    英文：按空格分割的单词算词
    """
    if not text:
        return 0
    
    import re
    # 移除标点符号和空格
    cleaned_text = re.sub(r'[^\w\s]', '', text)
    
    # 分离中文字符和英文单词
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', cleaned_text)
    english_words = re.findall(r'[a-zA-Z]+', cleaned_text)
    
    return len(chinese_chars) + len(english_words)