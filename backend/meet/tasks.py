import traceback
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.files import File as DjangoFile
import markdown
import os
import logging

from system.models import File
from .models import Recording, Speaker, Segment, MeetingSummary, Meeting, MeetingPhoto
from core.services.audio_processor import AudioProcessor
from .models import MeetingSummary, RealtimeRecordingSession
import requests
from openai import OpenAI
from weasyprint import HTML, CSS

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def process_recording_audio(self, session_id: str, audio_file_path: str, meeting_id: int):
    """
    后台处理录音：合并音频 -> 创建Recording -> 说话人分离和转写
    
    这个任务完全独立于WebSocket连接
    """

    try:
        session = RealtimeRecordingSession.objects.get(session_id=session_id)
        session.mark_processing()
    except RealtimeRecordingSession.DoesNotExist:
        logger.warning(f"会话 {session_id} 不存在，继续处理")
        session = None
    try:
        logger.info(f"开始处理实时录音: session={session_id}, file={audio_file_path}, meeting_id={meeting_id}")
        
        # 1. 验证音频文件
        if not os.path.exists(audio_file_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_file_path}")
        
        # 2. 获取Meeting对象
        meeting = Meeting.objects.get(id=meeting_id)
        
        # 3. 创建File记录
        with open(audio_file_path, 'rb') as f:
            # 使用 Django File 包装器
            django_file = DjangoFile(f, name=f'recording_{session_id}.webm')
            # 添加 size 属性
            django_file.size = os.path.getsize(audio_file_path)
            
            file_record = File.create_from_file(
                django_file, 
                name=f'recording_{session_id}.webm'
            )
        
        # 4. 创建Recording记录
        with transaction.atomic():
            recording = Recording.objects.create(
                meeting=meeting,
                file=file_record,
                name=f'实时录音 {session_id}',
                uploader_id=meeting.creator_id,  # 或从其他地方获取
                duration=None,
                process_status=0  # 待处理
            )
            
            # 设置会议状态为进行中
            if meeting.status != 1:
                meeting.status = 1
                meeting.save(update_fields=['status'])
        
        logger.info(f"Recording创建成功: {recording.id}")
        
        # 5. 调用现有的处理任务（就像upload一样）
        from meet.tasks import process_uploaded_audio
        process_uploaded_audio.delay(recording.id)
        
        # 标记会话为已完成
        if session:
            session.mark_completed(recording)

        # 清理临时文件
        try:
            os.remove(audio_file_path)
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")
        
        logger.info(f"录音处理完成: recording_id={recording.id}")
        return {'success': True, 'recording_id': recording.id}
        
    except Exception as e:
        logger.error(f"录音处理失败: {e}")
        import traceback
        traceback.print_exc()
        # 标记会话为失败
        if session:
            session.mark_failed(str(e))
        return {'success': False, 'error': str(e)}

@shared_task(bind=True)
def process_uploaded_audio(self, recording_id):
    """
    处理上传的音频文件的后台任务
    """
    try:
        # 1. 获取录音记录
        recording = Recording.objects.get(id=recording_id)
        recording.process_status = 1  # 处理中
        recording.save()
        
        logger.info(f'开始处理录音 {recording_id}')
        
        # 2. 获取文件路径
        audio_file_path = recording.file.url.path
        
        # 3. 创建输出目录
        temp_base = getattr(settings, 'MEETVOICE_TEMP_DIR', '/tmp/meetvoice')
        output_dir = os.path.join(temp_base, str(recording_id))
        os.makedirs(output_dir, exist_ok=True)
        
        # 4. 初始化音频处理器
        audio_processor = AudioProcessor()
        
        # 5. 获取合并的关键词
        hotwords = recording.get_all_keywords()
        
        # 6. 执行说话人分离和转录
        result = audio_processor.separate_speakers(
            media_path=audio_file_path,
            output_dir=output_dir,
            merge_threshold=10,
            save_audio_segments=True,
            save_merged_audio=True,
            hotwords=hotwords,
            progress_callback=lambda message, progress: self.update_state(
                state='PROGRESS',
                meta={
                    'current': progress, 
                    'total': 100,
                    'message': message
                }
            )
        )
        logger.info(f"\n\nprocess_uploaded_audio <<separate_speakers>> result: {result}\n\n")

        if not result or not result.get('success', False):
            raise Exception(f"音频处理失败: {result.get('message', '未知错误')}")
        
        # 7. 保存处理结果到数据库
        _save_processing_results(recording, result)
        
        # 8. 更新录音状态
        recording.process_status = 2  # 已完成
        if 'duration' in result:
            recording.duration = result['duration']
        recording.save()
        
        logger.info(f'录音 recordingid={recording_id} 处理完成')
        
        # 触发会议纪要生成任务
        generate_meeting_summary.delay(recording_id)
        
        return {
            'recording_id': recording_id,
            'status': 'completed',
            'speakers_count': len(result.get('speakers', [])),
            'message': '处理完成'
        }
        
    except Recording.DoesNotExist:
        error_msg = f'录音记录 {recording_id} 不存在'
        logger.error(error_msg)
        return {'error': error_msg}
        
    except Exception as e:
        error_msg = f'处理录音 {recording_id} 失败: {str(e)}'
        logger.error(error_msg)
        logger.error(f'错误详情: {traceback.format_exc()}')
        
        # 更新录音状态为失败
        try:
            recording = Recording.objects.get(id=recording_id)
            recording.process_status = 3
            recording.save()
        except:
            pass
            
        return {'error': error_msg}

@shared_task(bind=True)
def generate_meeting_summary(self, recording_id):
    """
    使用大模型生成会议纲要的后台任务
    """
    try:
        # 1. 获取录音记录
        recording = Recording.objects.get(id=recording_id)
        
        # 2. 获取或创建会议纲要记录
        summary, created = MeetingSummary.objects.get_or_create(
            meeting=recording.meeting,
            defaults={'generate_status': 1}  # 生成中
        )
        
        if not created:
            summary.generate_status = 1  # 生成中
            summary.save()
            
        # 3. 准备系统提示词
        system_prompt = """你是一个专业的会议纪要助手。请根据提供的会议文本：
1. 提取关键信息，包括主要议题、重要决策和行动项目
2. 按照时间顺序组织内容
3. 使用清晰的结构和专业的语言
4. 突出重要的结论和后续行动
5. 对每个参会者的发言进行简要总结，包括：
   - 主要观点和立场
   - 提出的建议或决策
   - 承诺的行动项目
请以如下结构输出会议纪要：

# 会议总体纪要
[在这里输出整体会议纪要]

# 参会人员发言要点
## 说话人1
[说话人1的主要观点和贡献总结]

## 说话人2
[说话人2的主要观点和贡献总结]

...以此类推"""

        # 4. 准备用户提示词 - 现在包含每个说话人的发言记录
        speakers_segments = {}
        for speaker in recording.speakers.all():
            segments = speaker.segments.all().order_by('start_time')
            speaker_text = "\n".join([
                f"[{segment.start_time.strftime('%H:%M:%S')} - {segment.end_time.strftime('%H:%M:%S')}] {segment.text}"
                for segment in segments
            ])
            speakers_segments[speaker.speaker_sequence] = speaker_text

        user_prompt = f"""这是一段会议记录的文本，请帮我总结会议纪要：

会议标题：{recording.meeting.title}
会议时间：{recording.meeting.start_time.strftime('%Y-%m-%d %H:%M')}

完整会议内容：
{recording.full_text}

各位参会者发言记录：
"""
        
        for speaker_sequence, text in speakers_segments.items():
            user_prompt += f"\n说话人{speaker_sequence}的发言：\n{text}\n"

        # 5. 首先尝试使用DeepSeek API
        try:
            client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com"
            )
            
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                stream=False
            )
            
            summary_content = response.choices[0].message.content
            
        except Exception as e:
            logger.warning(f"DeepSeek API调用失败，尝试使用星火API: {str(e)}")
            
            # 6. 如果DeepSeek失败，尝试使用星火API
            headers = {
                'Authorization': f"Bearer {settings.XUNFEI_API_KEY}",
                'content-type': "application/json"
            }
            
            body = {
                "model": "generalv3.5",
                "user": "system",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "stream": False
            }
            
            response = requests.post(
                url="https://spark-api-open.xf-yun.com/v1/chat/completions",
                json=body,
                headers=headers
            )
            
            if response.status_code != 200:
                raise Exception(f"星火API调用失败: {response.text}")
                
            result = response.json()
            if result.get('code') != 0:
                raise Exception(f"星火API返回错误: {result.get('message')}")
                
            summary_content = result['choices'][0]['message']['content']
        
        # 7. 保存生成的纪要
        summary.content = summary_content
        summary.generate_status = 2  # 已生成
        summary.save()

          # 8. 更新会议状态为已完成
        meeting = recording.meeting
        meeting.status = 2  # 已完成
        meeting.save()
        
        logger.info(f'会议 {recording.meeting.id} 的纪要生成完成')
        
        return {
            'meeting_id': recording.meeting.id,
            'status': 'completed',
            'message': '会议纪要生成完成'
        }
        
    except Recording.DoesNotExist:
        error_msg = f'录音记录 {recording_id} 不存在'
        logger.error(error_msg)
        return {'error': error_msg}
        
    except Exception as e:
        error_msg = f'生成会议纪要失败: {str(e)}'
        logger.error(error_msg)
        logger.error(f'错误详情: {traceback.format_exc()}')
        
        # 更新纪要状态为失败
        try:
            summary = MeetingSummary.objects.get(meeting=recording.meeting)
            summary.generate_status = 3  # 生成失败
            summary.save()
        except:
            pass
            
        return {'error': error_msg}

def _save_processing_results(recording, result):
    """保存处理结果到数据库"""
    
    # 1. 清除旧的相关记录（如果重新处理）
    recording.speakers.all().delete()
    recording.transcripts.all().delete()

    # 2. 保存完整转录文本
    recording.full_text = result.get('full_text', '')
    recording.save()
    
    # 2. 创建说话人
    speakers_map = {}
    for speaker_sequence, speaker_data in result.get('speakers', {}).items():
        speaker = Speaker.objects.create(
            recording=recording,
            speaker_sequence=str(speaker_sequence)
        )
        speakers_map[str(speaker_sequence)] = speaker
    
    # 3. 保存转录片段
    for speaker_sequence, speaker_data in result.get('speakers', {}).items():
        speaker = speakers_map.get(str(speaker_sequence))
        
        if speaker and 'segments' in speaker_data:
            for segment_data in speaker_data['segments']:
                # 直接使用时间字符串，格式已经符合 HH:MM:SS.mmm
                Segment.objects.create(
                    recording=recording,
                    speaker=speaker,
                    start_time=segment_data.get('start', '00:00:00.000'),
                    end_time=segment_data.get('end', '00:00:00.000'),
                    text=segment_data.get('text', ''),
                    confidence=1.0  # 如果API没有提供confidence，默认为1.0
                )


@shared_task
def generate_meeting_report_task(meetingid: int):
    """异步任务：生成会议报告 (PDF)"""
    try:
        summary = MeetingSummary.objects.get(meeting_id=meetingid)
        meeting = summary.meeting

        # 主持人 
        moderator = meeting.get_moderator()
        # 检查参会人员
        participants = meeting.participants.all()
        # 检查会议照片
        photos = meeting.photos.filter(photo_type=1)
        # 检查签到表
        sign_in_sheets = meeting.photos.filter(photo_type=2)
        
        # 构建HTML内容
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: "Source Han Sans SC";
                    line-height: 1.6;
                    padding: 40px;
                    color: #333;
                }}
                h1 {{
                    text-align: center;
                    color: #2c3e50;
                    border-bottom: 3px solid #3498db;
                    padding-bottom: 10px;
                    margin-bottom: 30px;
                }}
                h2 {{
                    color: #2980b9;
                    margin-top: 30px;
                    margin-bottom: 15px;
                    border-left: 4px solid #3498db;
                    padding-left: 10px;
                }}
                ul {{
                    list-style-type: none;
                    padding-left: 0;
                }}
                li {{
                    margin: 8px 0;
                    padding-left: 20px;
                    position: relative;
                }}
                li:before {{
                    content: "•";
                    position: absolute;
                    left: 0;
                    color: #3498db;
                }}
                img {{
                    max-width: 100%;
                    height: auto;
                    margin: 15px 0;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 5px;
                }}
                .photo-description {{
                    font-style: italic;
                    color: #666;
                    margin-top: -10px;
                    margin-bottom: 20px;
                }}
                .meeting-info {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    border-radius: 5px;
                    margin-bottom: 20px;
                }}
            </style>
        </head>
        <body>
            <h1>{meeting.title}会议报告</h1>
            
            <div class="meeting-info">
                <h2>会议基本信息</h2>
                <ul>
                    <li>会议时间：{meeting.start_time.strftime('%Y年%m月%d日 %H:%M')}</li>
                    <li>会议地点：{meeting.location_name or '未设置'}</li>
                    <li>主持人：{moderator.name} {moderator.title or ''} {moderator.company or ''}</li>
                </ul>
            </div>
            
            <h2>参会人员</h2>
            <ul>
        """
        
        # 添加参会人员信息
        for p in participants:
            if not p.is_moderator:
                html_content += f"<li>{p.name} {p.title or ''} {p.company or ''}</li>\n"
        
        html_content += "</ul>\n<h2>会议纲要</h2>\n"
        
        # 将markdown内容转换为HTML
        summary_html = markdown.markdown(summary.content)
        html_content += f"<div>{summary_html}</div>\n"
        
        # 添加会议照片
        html_content += "<h2>会议照片</h2>\n"
        for photo in photos:
            # 使用绝对路径
            photo_path = photo.file.url.path if hasattr(photo.file.url, 'path') else photo.file.path
            html_content += f'<img src="file://{photo_path}" alt="会议照片">\n'
            if photo.description:
                html_content += f'<p class="photo-description">{photo.description}</p>\n'
        
        # 添加签到表
        html_content += "<h2>签到表</h2>\n"
        for sheet in sign_in_sheets:
            sheet_path = sheet.file.url.path if hasattr(sheet.file.url, 'path') else sheet.file.path
            html_content += f'<img src="file://{sheet_path}" alt="签到表">\n'
        
        html_content += "</body></html>"
        
        # 生成PDF文件
        from django.core.files.base import ContentFile
        from system.models import File
        import io
        
        # 使用weasyprint生成PDF
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        # 保存PDF文件
        pdf_filename = f"{meeting.title}-会议报告.pdf"
        content_file = ContentFile(pdf_buffer.read(), name=pdf_filename)
        pdf_file = File.create_from_file(content_file, name=pdf_filename)
        
        # 更新状态
        summary.report_file = pdf_file
        summary.generate_status = 2  # 已生成
        summary.save()
        
        logger.info(f'会议 {meeting.id} 的PDF报告生成完成')
        
    except Exception as e:
        logger.error(f'生成会议报告失败: {str(e)}')
        logger.error(f'错误详情: {traceback.format_exc()}')
        try:
            summary.generate_status = 3  # 生成失败
            summary.save()
        except:
            pass
        raise e