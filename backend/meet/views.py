import json
import os
import mimetypes
import uuid
from django.http import JsonResponse, HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import render
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from celery import current_app
from .models import Meeting, Recording, Speaker, TranscriptSegment
from system.models import File
import logging

logger = logging.getLogger(__name__)

def index(request):
    return render(request, 'meet/index.html')

@require_http_methods(["GET"])
def get_processing_status(request, recording_id):
    """
    获取录音处理状态
    GET /api/transcription/status/{recording_id}/
    """
    try:
        recording = Recording.objects.get(id=recording_id)
        
        # 构建响应数据
        response_data = {
            'recording_id': recording_id,
            'meeting_id': recording.meeting.id,
            'process_status': recording.process_status,
            'status_text': recording.get_process_status_display(),
            'file_name': recording.file.name,
            'duration': recording.duration,
        }
        
        # 如果处理完成，添加结果信息
        if recording.process_status == 2:  # 已完成
            speakers = recording.speakers.all()
            transcripts = recording.transcripts.select_related('speaker').order_by('start_time')
            
            response_data.update({
                'speakers_count': speakers.count(),
                'speakers': [
                    {
                        'speaker_id': speaker.speaker_id,
                        'name': speaker.name or speaker.speaker_id,
                        'segments_count': speaker.segments.count()
                    }
                    for speaker in speakers
                ],
                'transcription': '\n'.join([
                    f"{segment.speaker.speaker_id}: {segment.text}"
                    for segment in transcripts
                ]),
                'segments': [
                    {
                        'speaker_id': segment.speaker.speaker_id,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'text': segment.text,
                        'confidence': segment.confidence
                    }
                    for segment in transcripts
                ],
                'processed_audio_url': f'/media/processed/{recording.id}/processed_audio.wav'
            })
        elif recording.process_status == 3:  # 处理失败
            response_data['error'] = '处理失败，请重试或联系管理员'
        
        return JsonResponse(response_data)
        
    except Recording.DoesNotExist:
        return JsonResponse({'error': '录音记录不存在'}, status=404)
    except Exception as e:
        logger.error(f'获取处理状态失败: {e}')
        return JsonResponse({'error': str(e)}, status=500)

def serve_processed_media(request, recording_id, filename):
    """
    提供处理后的音频文件下载服务
    GET /media/processed/{recording_id}/{filename}
    """
    try:
        # 验证录音记录是否存在
        recording = Recording.objects.get(id=recording_id)
        
        # 构建文件路径
        temp_base = getattr(settings, 'MEETVOICE_TEMP_DIR', '/tmp/meetvoice')
        file_path = os.path.join(temp_base, str(recording_id), filename)
        
        # 安全检查：确保文件路径在允许的目录内
        if not os.path.abspath(file_path).startswith(os.path.abspath(temp_base)):
            raise Http404("文件不存在")
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise Http404("文件不存在")
        
        # 获取文件MIME类型
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            if filename.endswith('.wav'):
                content_type = 'audio/wav'
            elif filename.endswith('.mp3'):
                content_type = 'audio/mpeg'
            elif filename.endswith('.webm'):
                content_type = 'audio/webm'
            else:
                content_type = 'application/octet-stream'
        
        # 读取并返回文件
        with open(file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = os.path.getsize(file_path)
            return response
            
    except Recording.DoesNotExist:
        raise Http404("录音记录不存在")
    except Http404:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': f'文件服务错误: {str(e)}'}, status=500)