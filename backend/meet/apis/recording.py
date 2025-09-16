from typing import List
import traceback

import mimetypes
import zipfile
import tempfile
from django.http import FileResponse
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.contrib.auth import get_user_model
from urllib.parse import quote
from ninja.pagination import paginate
from ninja import Field, ModelSchema, Query, Router
from utils.meet_auth import data_permission
from utils.meet_crud import create, delete, retrieve, update
from utils.meet_ninja import MeetFilters, MyPagination
from pydantic import computed_field
from utils.usual import get_user_info_from_token
from system.models import File
from meet.models import Meeting, Recording, Speaker, Segment
from utils.meet_crud import delete, retrieve
from utils.meet_ninja import MeetFilters, MyPagination
from utils.meet_response import MeetResponse, MeetError, BusinessCode
from meet.permissions import (
    require_meeting_permission,
    require_meeting_view_permission,
    require_meeting_owner,
    require_recording_owner,
    require_recording_view_permission
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


class RecordingUpdateSchemaIn(ModelSchema):
    recordingid: int = Field(..., description="录音ID")
    name: str = Field(None, description="录音名称")
    keywords: str = Field(None, description="录音关键词")
    upload_location: str = Field(None, description="上传地点")

    class Config:
        model = Recording
        model_exclude = ['id', 'meeting', 'file', 'uploader', 'create_datetime', 'update_datetime', 'process_status']


class RecordingSchemaOut(ModelSchema):
    class Config:
        model = Recording
        model_fields = "__all__"

    @computed_field(description="处理状态")
    def status_text(self) -> str | None:
        if self.process_status is not None:
            # 从 PROCESS_STATUS_CHOICES 中获取对应的显示文本
            for status_value, status_display in Recording.PROCESS_STATUS_CHOICES:
                if status_value == self.process_status:
                    return status_display
        return None

    @computed_field(description="会议ID")
    def meetingid(self) -> int | None:
        return self.meeting if self.meeting else None

    @computed_field(description="文件ID")
    def fileid(self) -> int | None:
        return self.file if self.file else None

    @computed_field(description="上传者ID")
    def uploaderid(self) -> int | None:
        return self.uploader if self.uploader else None


@router.post("/recording/create", response=RecordingSchemaOut)
@require_meeting_owner
def create_recording(request, meetingid: int=Query(...)):
    """
    上传音频文件接口
    只有会议所有者可以上传音频文件
    一场会议只能上传一个录音文件
    """
    try:
        # 验证会议是否存在
        meeting = get_object_or_404(Meeting, id=meetingid)

         # 检查是否可以上传
        can_upload, message = meeting.can_upload_recording()
        if not can_upload:
            raise MeetError(message, BusinessCode.BUSINESS_ERROR.value)

        if 'audio' not in request.FILES:
            raise MeetError('没有上传音频文件', BusinessCode.BUSINESS_ERROR.value)

        audio_file = request.FILES['audio']
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
                name=file_info['name'],
                uploader_id=request_user['id'],
                duration=None,
                process_status=0
            )
            # INSERT_YOUR_CODE
            # 创建录音后，将会议状态设为进行中（1）
            if meeting.status != 1:
                meeting.status = 1
                meeting.save(update_fields=['status'])

            # 3. 启动后台处理任务
            from meet.tasks import process_uploaded_audio
            task = process_uploaded_audio.delay(recording.id)

            logger.info(f'音频文件上传成功，录音ID: {recording.id}, 任务ID: {task.id}, 上传者ID: {request_user["id"]}')

            return recording

    except MeetError as e:
        raise e
    except Exception as e:
        logger.error(f'音频文件上传失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError('服务器错误，上传失败', BusinessCode.SERVER_ERROR.value)

@router.post("/recording/update", response=RecordingSchemaOut)
@require_recording_owner
def update_recording(request, data: RecordingUpdateSchemaIn):
    """
    更新录音信息接口
    只有会议所有者可以更新录音信息
    """
    try:
        # 获取录音对象
        recording = get_object_or_404(Recording, id=data.recordingid)
        meeting = recording.meeting
        
        # 验证会议状态
        if meeting.status == 2:
            raise MeetError('会议已结束，无法修改录音信息', BusinessCode.BUSINESS_ERROR.value)
        if meeting.status == 3:
            raise MeetError('会议已取消，无法修改录音信息', BusinessCode.BUSINESS_ERROR.value)
        
        # 更新录音信息
        update_data = data.dict(exclude_unset=True)  # 只更新提供的字段
        for field, value in update_data.items():
            if hasattr(recording, field):
                setattr(recording, field, value)
        
        recording.save()
        
        logger.info(f'录音信息更新成功，录音ID: {recording.id}, 更新字段: {list(update_data.keys())}')
        return recording
        
    except MeetError as e:
        raise e
    except Exception as e:
        logger.error(f'录音信息更新失败: {e}')
        raise MeetError('服务器错误，更新失败', BusinessCode.SERVER_ERROR.value)

@router.get("/recording/delete")
@require_recording_owner
def delete_recording(request, recordingid: int=Query(...)):
    """删除录音文件"""
    # 获取录音对象
    recording = get_object_or_404(Recording, id=recordingid)
    meeting = recording.meeting
    if meeting.status == 2:
        raise MeetError('会议已结束，无法删除录音', BusinessCode.BUSINESS_ERROR.value)
    
    if meeting.status == 3:
        raise MeetError('会议已取消，无法删除录音', BusinessCode.BUSINESS_ERROR.value)

    if recording.file:
        recording.file.delete()  # 这会同时删除物理文件

    delete(recordingid, Recording)
    return MeetResponse(errcode=BusinessCode.OK)

@router.get("/meeting/recording/list", response=List[RecordingSchemaOut])
@require_meeting_view_permission
def list_meeting_recordings(request, meetingid: int=Query(...)):
    """获取指定会议的所有录音"""
    meeting = get_object_or_404(Meeting, id=meetingid)
    recordings = Recording.objects.filter(meeting=meeting).order_by('-create_datetime')
    return list(recordings)


@router.get("/recording/get")
@require_recording_view_permission
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
            'recordingid': recordingid,
            'meetingid': recording.meeting.id,
            'meeting_title': recording.meeting.title,
            'process_status': recording.process_status,
            'status_text': recording.get_process_status_display(),
            'file_name': recording.file.name if recording.file else None,
            'file_size': recording.file.size if recording.file else None,
            'file_uuid': str(recording.file.uuid) if recording.file else None,  # 改为返回UUID
            'duration': recording.duration,
            'upload_time': recording.create_datetime,
            'uploader_name': recording.uploader.name if recording.uploader else None,
            'keywords': recording.get_keywords_string(),
        }
        
        # 根据处理状态添加详细信息
        if recording.process_status == 2:  # 已完成
            # 获取说话人信息
            speakers = recording.speakers.all().order_by('speaker_sequence')
            
            # 获取转录片段，优化查询性能
            transcripts = recording.transcripts.select_related('speaker').order_by('start_time')
            
            # 构建说话人列表
            speakers_list = []
            for speaker in speakers:
                speakers_list.append({
                    'speakerid': speaker.id,
                    'speaker_sequence': speaker.speaker_sequence,
                    'speaker_name': speaker.name or speaker.speaker_sequence,
                    'title': speaker.title,
                    'department': speaker.department,
                    'company': speaker.company,
                    'segments_count': speaker.segments.count(),
                    'total_speech_time': _calculate_speaker_total_time(speaker)
                })
            
            # 构建转录文本（完整版本）
            full_transcription = '\n'.join([
                f"[{segment.start_time.strftime('%H:%M:%S')}] {segment.speaker.speaker_sequence}: {segment.text}"
                for segment in transcripts
            ])
            
            # 构建分段列表
            segments_list = []
            for segment in transcripts:
                segments_list.append({
                    'segmentid': segment.id,
                    'speakerid': segment.speaker.id,
                    'speaker_sequence': segment.speaker.speaker_sequence,
                    'speaker_name': segment.speaker.name or segment.speaker.speaker_sequence,
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
            
        elif recording.process_status == 3:
            response_data.update({
                'error': '处理失败，请重试或联系管理员',
                'error_details': '音频文件处理过程中出现错误，请检查文件格式或重新上传',
                'retry_url': f'/api/transcription/retry/{recording.id}/'
            })
            
        elif recording.process_status == 1: 
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
    
@router.get("/recording/download")
@require_recording_view_permission
def download_recording_file(request, recordingid: int=Query(...)):
    """
    下载录音文件接口 - 直接返回文件流
    GET /api/recording/download?recordingid=123
    
    参数:
    - recordingid: 录音ID，必填
    """
    try:
        # 获取录音记录
        recording = Recording.objects.select_related('meeting', 'file').get(id=recordingid)    

        if not recording.file:
            raise MeetError('原始录音文件不存在', BusinessCode.INSTANCE_NOT_FOUND.value)
        
        file_path = recording.file.url.path
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise MeetError('录音文件不存在', BusinessCode.INSTANCE_NOT_FOUND.value)
        
        # 记录下载请求日志
        request_user = get_user_info_from_token(request)
        logger.info(f'用户 {request_user["id"]} 下载录音文件: {recordingid}, 文件: {recording.file.name}')
        
        # 构建下载文件名：会议名_录音文件名
        meeting_title = recording.meeting.title if recording.meeting.title else "未命名会议"
        # 清理文件名中的特殊字符，避免文件系统问题
        safe_meeting_title = "".join(c for c in meeting_title if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_meeting_title = safe_meeting_title.replace(' ', '_')
        
        # 获取原始文件扩展名
        original_filename = recording.file.name
        
        # 构建新的下载文件名
        download_filename = f"{safe_meeting_title}_{recording.id}_{os.path.basename(recording.file.name)}"
        download_filename_quoted = quote(download_filename)

        # 获取文件MIME类型
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            if recording.file.name.lower().endswith('.wav'):
                content_type = 'audio/wav'
            elif recording.file.name.lower().endswith('.mp3'):
                content_type = 'audio/mpeg'
            elif recording.file.name.lower().endswith('.m4a'):
                content_type = 'audio/mp4'
            else:
                content_type = 'application/octet-stream'
        
        response = FileResponse(
            open(file_path, 'rb'),
            content_type=content_type,
            as_attachment=True,
        )

        response['Content-Disposition'] = f'attachment; filename="{download_filename_quoted}"'
        
        # 设置文件大小
        response['Content-Length'] = os.path.getsize(file_path)        
        # 支持断点续传
        response['Accept-Ranges'] = 'bytes'
        
        return response
        
    except Recording.DoesNotExist:
        raise MeetError('录音记录不存在', BusinessCode.INSTANCE_NOT_FOUND.value)
    except MeetError:
        raise
    except Exception as e:
        logger.error(f'下载录音文件失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError('服务器错误，下载文件失败', BusinessCode.SERVER_ERROR.value)

@router.get("/recording/download/batch")
@require_meeting_view_permission
def batch_download_recordings_zip(request, meetingid: int=Query(...), file_type: str=Query("original")):
    """
    批量下载会议录音文件接口 - 直接返回ZIP文件
    GET /api/recording/download/batch/zip?meetingid=123
    
    参数:
    - meetingid: 会议ID，必填
    - file_type: 文件类型，默认为"original"
    
    功能说明:
    1. 获取会议下所有录音文件
    2. 将所有录音文件打包成ZIP文件
    3. 直接返回ZIP文件供下载
    """
    try:
        # 获取会议下的所有录音
        meeting = Meeting.objects.get(id=meetingid)
        recordings = Recording.objects.filter(meeting=meeting).order_by('-create_datetime')
        
        if not recordings.exists():
            raise MeetError('该会议没有录音文件', BusinessCode.INSTANCE_NOT_FOUND.value)
        
        # 记录批量下载请求日志
        request_user = get_user_info_from_token(request)
        logger.info(f'用户 {request_user["id"]} 请求批量下载ZIP文件，会议: {meetingid}，类型: {file_type}')
        
        # 创建临时ZIP文件
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_zip.close()
        
        try:
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                added_files = 0
                total_size = 0
                
                for recording in recordings:
                    try:
                        if not recording.file:
                            logger.warning(f'录音 {recording.id} 没有文件，跳过')
                            continue
                            
                        file_path = recording.file.url.path
                        
                        # 检查文件是否存在
                        if not os.path.exists(file_path):
                            logger.warning(f'录音文件不存在: {file_path}')
                            continue
                        
                        # 构建ZIP内的文件名
                        meeting_title = recording.meeting.title if recording.meeting.title else "未命名会议"
                        safe_meeting_title = "".join(c for c in meeting_title if c.isalnum() or c in (' ', '-', '_')).strip()
                        safe_meeting_title = safe_meeting_title.replace(' ', '_')
                        
                        # 获取原始文件名和扩展名
                        original_filename = recording.file.name
                        
                        # 构建ZIP内的文件名：会议名_录音ID_原始文件名
                        zip_filename = f"{safe_meeting_title}_{recording.id}_{os.path.basename(original_filename)}"
                        
                        # 添加到ZIP文件
                        zip_file.write(file_path, zip_filename)
                        added_files += 1
                        total_size += os.path.getsize(file_path)
                        
                        logger.debug(f'已添加文件到ZIP: {zip_filename}')
                        
                    except Exception as e:
                        logger.warning(f'处理录音 {recording.id} 时出错: {e}')
                        continue
                
                if added_files == 0:
                    raise MeetError('没有可下载的录音文件', BusinessCode.INSTANCE_NOT_FOUND.value)
                
                logger.info(f'ZIP文件创建完成，包含 {added_files} 个文件，总大小: {total_size} 字节')
        
        except Exception as e:
            # 清理临时文件
            if os.path.exists(temp_zip.name):
                os.unlink(temp_zip.name)
            raise e
        
        # 构建下载文件名
        safe_meeting_title = "".join(c for c in meeting.title if c.isalnum() or c in (' ', '-', '_')).strip() if meeting.title else "未命名会议"
        safe_meeting_title = safe_meeting_title.replace(' ', '_')
        zip_download_name = f"{safe_meeting_title}_录音文件_{added_files}个文件.zip"
        zip_download_name_quoted = quote(zip_download_name)
        
        # 使用FileResponse返回ZIP文件
        response = FileResponse(
            open(temp_zip.name, 'rb'),
            content_type='application/zip',
            as_attachment=True,
        )
        
        response['Content-Disposition'] = f'attachment; filename="{zip_download_name_quoted}"'
        response['Accept-Ranges'] = 'bytes'
        
        # 设置文件大小
        response['Content-Length'] = os.path.getsize(temp_zip.name)
        
        # 添加清理临时文件的回调
        def cleanup_temp_file():
            try:
                if os.path.exists(temp_zip.name):
                    os.unlink(temp_zip.name)
            except Exception as e:
                logger.warning(f'清理临时文件失败: {e}')
        
        # 在响应关闭时清理临时文件
        response.closed = cleanup_temp_file
        
        logger.info(f'用户 {request_user["id"]} 成功下载ZIP文件: {zip_download_name}')
        
        return response
        
    except Meeting.DoesNotExist:
        raise MeetError('会议不存在', BusinessCode.INSTANCE_NOT_FOUND.value)
    except MeetError:
        raise
    except Exception as e:
        logger.error(f'批量下载ZIP文件失败: {e}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        raise MeetError('服务器错误，批量下载ZIP文件失败', BusinessCode.SERVER_ERROR.value)

# ============= Speaker 说话人相关接口 =============

class SpeakerFilters(MeetFilters):
    meetingid: int = Field(None, description="会议ID")
    recordingid: int = Field(None, description="录音ID")
    speakerid: int = Field(None, description="说话人ID")


class SpeakerSchemaIn(ModelSchema):
    id: int = Field(..., description="说话人ID", alias="speakerid")
    
    class Config:
        model = Speaker
        model_exclude = ['id', 'recording', 'speaker_sequence', 'create_datetime', 'update_datetime']


class SpeakerSchemaOut(ModelSchema):
    speakerid: int = Field(..., alias="id")
    class Config:
        model = Speaker
        model_exclude = ['id', 'create_datetime', 'update_datetime']

    @computed_field(description="录音ID")
    def recordingid(self) -> int | None:
        return self.recording if self.recording else None
    
    @computed_field(description="会议ID")
    def meetingid(self) -> int | None:
        meetingid = Recording.objects.get(id=self.recording).meeting.id if self.recording else None
        return meetingid

@router.post("/speaker/update", response=SpeakerSchemaOut)
@require_meeting_permission('edit')
def update_speaker(request, data: SpeakerSchemaIn):
    """更新说话人信息"""
    speaker = update(request, data.id, data, Speaker)
    return speaker


@router.post("/speaker/list", response=List[SpeakerSchemaOut])
@require_meeting_permission('view')
@paginate(MyPagination)
def list_speaker(request, filters: SpeakerFilters):

    filters = data_permission(request, filters)   
    filter_mapping = {
        'meetingid': 'recording__meeting_id',
        'recordingid': 'recording_id', 
        'speakerid': 'id'
    }    
    filter_kwargs = {
        filter_mapping[key]: getattr(filters, key)
        for key in filter_mapping
        if getattr(filters, key) is not None
    }    
    return Speaker.objects.filter(**filter_kwargs)


@router.get("/speaker/get", response=SpeakerSchemaOut)
@require_meeting_permission('view')
def get_speaker(request, speakerid: int = Query(...)):
    """获取说话人详情"""
    speaker = get_object_or_404(Speaker, id=speakerid)
    return speaker


# ============= Segment 转录片段相关接口 =============

class SegmentFilters(MeetFilters):
    recordingid: int = Field(None, alias="recordingid")
    speakerid: int = Field(None, alias="speakerid")
    text: str = Field(None)


class SegmentSchemaIn(ModelSchema):
    id: int = Field(..., description="转录片段ID", alias="segmentid")
    
    class Config:
        model = Segment
        model_exclude = ['id', 'create_datetime', 'update_datetime']


class SegmentSchemaOut(ModelSchema):
    segmentid: int = Field(..., description="转录片段ID", alias="id")
    class Config:
        model = Segment
        model_exclude = ['id', 'create_datetime', 'update_datetime']

    @computed_field(description="说话人ID")
    def speakerid(self) -> int | None:
        return self.speaker if self.speaker else None
    
    @computed_field(description="录音ID")
    def recordingid(self) -> int | None:
        recordingid = Recording.objects.get(id=self.recording).id if self.recording else None
        return recordingid

@router.post("/segment/list", response=List[SegmentSchemaOut])
@require_meeting_permission('view')
@paginate(MyPagination)
def list_segment(request, filters: SegmentFilters):
    """分页获取转录片段列表"""
    filters = data_permission(request, filters)       
    queryset = Segment.objects.all()    
    if filters.recordingid is not None:
        queryset = queryset.filter(recording_id=filters.recordingid)    
    if filters.speakerid is not None:
        queryset = queryset.filter(speaker_id=filters.speakerid)  
    if filters.text is not None:
        queryset = queryset.filter(text__icontains=filters.text)
    
    return queryset


@router.post("/segment/get", response=SegmentSchemaOut)
@require_meeting_permission('view')
def get_transcript(request, segmentid: int=Query(...)):
    """获取转录片段详情"""
    segment = get_object_or_404(Segment, id=segmentid)
    return segment

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

def _calculate_segment_duration(segment: Segment) -> float:
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

    