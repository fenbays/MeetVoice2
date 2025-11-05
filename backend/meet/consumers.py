import json
import asyncio
import logging
import datetime
import tempfile
import os
import uuid
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db import transaction
from .models import Meeting, Recording, RealtimeRecordingSession
from django.utils import timezone

# 集成src目录的服务
import sys
from core.services.audio_processor import AudioProcessor
from core.services.streaming_speech_service import StreamingSpeechService
from core.utils.model_manager import ModelManager
from conf.model import ModelConfig

logger = logging.getLogger(__name__)

class TranscriptionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """初始化连接"""
        self.connected = True  # 连接状态标记
        self.meeting_id = self.scope['url_route']['kwargs']['meeting_id']
        self.room_group_name = f'transcribe_{self.meeting_id}'
        
        # 创建会话
        try:
            self.session = await self._get_or_create_session()
            self.session_id = self.session.session_id
        except ValueError as e:
            logger.warning(f"实时录音连接被拒绝: {str(e)}, meeting_id={self.meeting_id}")
            await self.accept()
            await self.send_response(
                action='start',
                status='warning',
                message=str(e),
                code='CANNOT_START',
                data={
                    
                }
            )
            await self.close(code=4003)
            return
        
        # 初始化状态（从数据库恢复）
        self.transcription_active = (self.session.recording_status == 1)  # 录音中
        self.is_paused = (self.session.recording_status == 2)  # 已暂停
        
        # 临时目录和文件路径
        from django.conf import settings
        temp_base = getattr(settings, 'MEETVOICE_TEMP_DIR', '/tmp/meetvoice')
        os.makedirs(temp_base, exist_ok=True)
        self.temp_dir = os.path.join(temp_base, self.session_id)
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 使用数据库中的路径或创建新路径
        if self.session.temp_audio_path:
            self.temp_audio_file = self.session.temp_audio_path
        else:
            self.temp_audio_file = os.path.join(self.temp_dir, f'recording_{self.session_id}.webm')
            await self._update_session_audio_path(self.temp_audio_file)
        
        self.audio_file_handle = None
        
        # 初始化转录服务
        try:
            # 只初始化AudioProcessor，它内部管理所有服务
            self.audio_processor = AudioProcessor(
                sample_rate=16000,
                channels=1,
                chunk_duration=5.0
            )
            
            # 设置回调函数
            self.audio_processor.set_transcription_callback(self._handle_transcription_result)
            self.audio_processor.set_error_callback(self._handle_audio_error)
            
        except Exception as e:
            logger.error(f"初始化转录服务失败: {e}")
            await self.close()
            return
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # ← 发送当前状态给前端
        await self.send_response(
            action='connect',
            status='success',
            message='会话已连接',
            data={
                'recording_status': self.session.recording_status,
                'recording_status_display': dict(RealtimeRecordingSession.RECORDING_STATUS_CHOICES)[self.session.recording_status],
                'pause_count': self.session.pause_count,
                'audio_size': self.session.audio_size,
                'can_resume': self.session.recording_status == 2,
                'resume_hint': '检测到未完成的录音，可以继续录音' if self.session.recording_status == 2 else None
            }
        )
        
        logger.info(f"转录会话 {self.session_id} 已连接，状态: {self.session.get_recording_status_display()}")
    
    async def disconnect(self, close_code):
        """断开连接：只清理WebSocket相关资源"""
        self.connected = False
        logger.info(f"会议 {self.meeting_id} 断开连接，close_code={close_code}")
        
        # ← 新增：如果正在录音，自动暂停并保存状态（支持断线重连后继续）
        if hasattr(self, 'session') and self.session:
            try:
                # 刷新会话状态
                await self._refresh_session()
                
                # 如果正在录音，自动暂停
                if self.session.recording_status == 1:  # 录音中
                    logger.info(f"会话 {self.meeting_id} 断开时正在录音，自动暂停")
                    
                    # 关闭文件句柄并刷新数据
                    if hasattr(self, 'audio_file_handle') and self.audio_file_handle:
                        try:
                            self.audio_file_handle.flush()
                            self.audio_file_handle.close()
                            self.audio_file_handle = None
                        except Exception as e:
                            logger.error(f"关闭文件句柄失败: {e}")
                    
                    # 更新数据库状态为暂停
                    await self._session_pause_recording()
                    await self._session_update_audio_info()
                    logger.info(f"会议 {self.meeting_id} 已自动暂停，用户可稍后重连继续录音")
            except Exception as e:
                logger.error(f"保存会话状态失败: {e}")
        
        # 停止接收音频
        self.transcription_active = False
        
        # 关闭文件句柄（如果还开着）
        if hasattr(self, 'audio_file_handle') and self.audio_file_handle:
            try:
                self.audio_file_handle.close()
            except Exception as e:
                logger.error(f"关闭文件句柄失败: {e}")
        
        # 清理AudioProcessor（保留临时文件和会话状态）
        if hasattr(self, 'audio_processor') and self.audio_processor:
            try:
                await self.audio_processor.cleanup()
            except Exception as e:
                logger.error(f"AudioProcessor清理失败: {e}")
        
        # 离开群组
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        
        logger.info(f"会话 {self.meeting_id} 清理完成")
    
    async def receive(self, text_data=None, bytes_data=None):
        """接收消息处理"""
        try:
            if text_data:
                # 控制消息必须是有效的JSON
                try:
                    data = json.loads(text_data)
                    if not isinstance(data, dict):
                        raise ValueError("消息必须是JSON对象")
                    await self.handle_control_message(data)
                except json.JSONDecodeError:
                    await self.send_response('message', 'error', '无效的JSON格式', code='INVALID_JSON')
                except ValueError as e:
                    await self.send_response('message', 'error', str(e), code='INVALID_MESSAGE')
            elif bytes_data:
                # 音频数据处理
                # if not getattr(self, 'transcription_active', False) and not getattr(self, 'is_paused', False):
                #     # 只有在既没有激活转录，也不是暂停状态时才返回警告
                #     await self.send_response('audio', 'warning', '请先发送start_transcription命令', code='NOT_STARTED')
                #     return
                await self.handle_audio_data(bytes_data)
            else:
                await self.send_response('message', 'error', '无效的消息格式', code='INVALID_FORMAT')
        except Exception as e:
            logger.error(f"消息处理错误: {e}")
            await self.send_response('message', 'error', str(e), code='PROCESSING_ERROR')
    
    async def handle_control_message(self, data):
        """处理控制消息"""
        message_type = data.get('type')
        
        if message_type == 'start_transcription':
            # ← 检查数据库状态
            can_start, msg = await self._check_can_start()
            if not can_start:
                await self.send_response(
                    action='start',
                    status='warning',
                    message=msg,
                    code='CANNOT_START',
                    data={'recording_status': self.session.recording_status, 'can_resume': self.session.recording_status == 2}
                )
                return
                
            """
            启动流程：
            1. 发送"开始加载模型"消息
            2. 异步加载模型 + 启动AudioProcessor
            3. 发送"模型加载完成"消息
            """
            # 立即响应，告诉前端开始加载
            await self.send_response(
                action='start',
                status='success',
                message='正在初始化音频处理系统...',
                data={'stage': 'initializing'}
            )
            
            # 异步执行初始化（只调用一次）
            asyncio.create_task(self._initialize_and_start_processing())
        
        elif message_type == 'pause_transcription':
            can_pause, msg = await self._check_can_pause()
            if not can_pause:
                await self.send_response(
                    action='pause',
                    status='warning',
                    message=msg,
                    code='CANNOT_PAUSE'
                )
                return
            await self._handle_pause_transcription()
        
        elif message_type == 'resume_transcription':
            can_resume, msg = await self._check_can_resume()
            if not can_resume:
                await self.send_response(
                    action='resume',
                    status='warning',
                    message=msg,
                    code='CANNOT_RESUME'
                )
                return
            
            # 立即响应，然后异步处理
            await self.send_response(
                action='resume',
                status='success',
                message='正在恢复录音...',
                data={'stage': 'initializing'}
            )
            
            # 异步执行恢复流程
            asyncio.create_task(self._initialize_and_resume_processing())
        
        elif message_type == 'stop_transcription':
            can_stop, msg = await self._check_can_stop()
            if not can_stop:
                await self.send_response(
                    action='stop',
                    status='warning',
                    message=msg,
                    code='CANNOT_STOP'
                )
                return
            await self._handle_stop_transcription()
                
        elif message_type == 'ping':
            # 心跳
            await self.send_response(
                action='ping',
                status='success',
                message='',
                data={'timestamp': datetime.datetime.now().isoformat()}
            )
        else:
            await self.send_response(
                action='message',
                status='warning',
                message=f"不支持的消息类型: {message_type}",
                code='UNSUPPORTED_TYPE'
            )

    async def _prepare_audio_processor(self, is_resume=False):
        """
        准备并启动 AudioProcessor
        参数：
            is_resume: 是否是恢复录音（影响进度消息的 action）
        返回：
            success: bool
        """
        action_name = 'resume' if is_resume else 'start'
        
        try:
            # 检查 AudioProcessor 状态
            if not hasattr(self, 'audio_processor') or not self.audio_processor:
                raise Exception("AudioProcessor不可用")
            
            if self.audio_processor._state == "RUNNING":
                logger.info("AudioProcessor已在运行")
                return True
            
            if self.audio_processor._state == "STOPPED":
                raise Exception("AudioProcessor已停止，无法重新启动")
            
            # 1. 通知：模型正在准备
            await self.send_response(
                action='model_loading',
                status='success',
                message='正在加载AI模型...',
                data={'stage': 'loading', 'progress': 0}
            )
            
            # 2. 定义进度回调
            async def progress_callback(stage: str, message: str):
                """模型加载进度回调"""
                await self.send_response(
                    action='model_loading',
                    status='success',
                    message=message,
                    data={'stage': stage}
                )
            
            # 3. 带回调地加载模型
            logger.info("开始加载模型...")
            if hasattr(self.audio_processor, 'prepare_streaming_models'):
                success = await self.audio_processor.prepare_streaming_models(
                    progress_callback=progress_callback
                )
                if not success:
                    raise Exception("模型准备失败")
            else:
                raise Exception("AudioProcessor 不支持流式模型")
            
            logger.info("模型加载完成")
            
            # 4. 启动AudioProcessor
            logger.info("启动AudioProcessor...")
            self.results_generator = await self.audio_processor.create_tasks()
            
            # 5. 启动结果处理任务
            if not hasattr(self, 'result_handler_task') or self.result_handler_task.done():
                self.result_handler_task = asyncio.create_task(self._handle_results())
            
            logger.info("AudioProcessor已启动，状态: RUNNING")
            return True
            
        except Exception as e:
            logger.error(f"准备AudioProcessor失败: {e}")
            await self.send_response(
                action=action_name,
                status='error',
                message=f'音频处理系统初始化失败: {str(e)}',
                code='INIT_FAILED'
            )
            return False

    async def _initialize_and_start_processing(self):
        """
        初始化并启动音频处理（首次开始录音）
        """
        try:
            # 立即响应
            await self.send_response(
                action='start',
                status='success',
                message='正在初始化音频处理系统...',
                data={'stage': 'initializing'}
            )
            
            # 准备并启动 AudioProcessor
            success = await self._prepare_audio_processor(is_resume=False)
            if not success:
                return
            
            # 更新数据库状态为"录音中"
            await self._session_start_recording()
            
            # 设置转录激活标志
            self.transcription_active = True
            self.is_paused = False
            
            # 发送完成消息
            await self.send_response(
                action='start',
                status='success',
                message='音频处理系统已就绪，可以开始录音',
                data={'stage': 'ready', 'recording_status': 1}
            )
            
            logger.info("音频处理系统启动完成")
            
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            import traceback
            traceback.print_exc()

            try:
                await self._session_mark_failed(f"初始化失败: {str(e)}")
            except Exception as db_error:
                logger.error(f"更新失败状态到数据库失败: {db_error}")
            finally:
                await self._cleanup_audio_processor()

    async def _handle_pause_transcription(self):
        """处理暂停"""
        logger.info(f"暂停录音")
        
        # 1. 设置内存标志
        self.is_paused = True
        
        # 2. 关闭文件句柄
        if self.audio_file_handle:
            try:
                self.audio_file_handle.flush()
                self.audio_file_handle.close()
                self.audio_file_handle = None
            except Exception as e:
                logger.error(f"关闭文件句柄失败: {e}")
        
        # 3. 更新数据库状态
        await self._session_pause_recording()
        await self._session_update_audio_info()
        
        # 4. 刷新session对象
        await self._refresh_session()
        
        # 5. 响应前端
        await self.send_response(
            action='pause',
            status='success',
            message='录音已暂停',
            data={
                'pause_count': self.session.pause_count,
                'audio_file_size': self.session.audio_size
            }
        )

    async def _initialize_and_resume_processing(self):
        """
        初始化并恢复音频处理（恢复录音）
        """
        try:
            # 1. 检查文件存在
            if not os.path.exists(self.temp_audio_file):
                raise Exception(f"音频文件不存在: {self.temp_audio_file}")
            
            # 2. 准备并启动 AudioProcessor
            success = await self._prepare_audio_processor(is_resume=True)
            if not success:
                return
            
            # 3. 以追加模式打开文件
            self.audio_file_handle = open(self.temp_audio_file, 'ab')
            
            # 4. 更新数据库状态
            await self._session_resume_recording()
            
            # 5. 设置内存标志
            self.is_paused = False
            self.transcription_active = True
            
            # 6. 刷新session对象
            await self._refresh_session()
            
            # 7. 发送完成消息（重要：只有这时前端才能开始发送音频）
            await self.send_response(
                action='resume',
                status='success',
                message='录音已恢复，可以继续录音',
                data={
                    'stage': 'ready',  # 告诉前端已经准备好了
                    'pause_count': self.session.pause_count,
                    'audio_file_size': self.session.audio_size
                }
            )
            
            logger.info("录音恢复完成")
            
        except Exception as e:
            logger.error(f"恢复录音失败: {e}")
            await self.send_response(
                action='resume',
                status='error',
                message=f"恢复录音失败: {str(e)}",
                code='RESUME_FAILED'
            )

    async def _handle_stop_transcription(self):
        """处理停止"""
        logger.info(f"停止录音")
        
        # 1. 设置标志
        self.transcription_active = False
        self.is_paused = False
        
        # 2. 关闭文件句柄
        if self.audio_file_handle:
            try:
                self.audio_file_handle.flush()
                self.audio_file_handle.close()
                self.audio_file_handle = None
            except Exception as e:
                logger.error(f"关闭文件句柄失败: {e}")
        
        # 3. 更新音频信息
        await self._session_update_audio_info()
        
        # 4. 验证文件
        if not os.path.exists(self.temp_audio_file):
            logger.error("录音文件不存在")
            await self.send_response(
                action='stop',
                status='error',
                message='录音文件不存在',
                code='FILE_NOT_FOUND'
            )
            return
        
        # 5. 启动后台任务
        from meet.tasks import process_recording_audio
        task = process_recording_audio.delay(
            session_id=self.session_id,
            audio_file_path=self.temp_audio_file,
            meeting_id=self.meeting_id
        )
        
        # 6. 更新数据库状态
        await self._session_stop_recording(task_id=task.id)
        await self._refresh_session()
        
        # 7. 响应前端
        await self.send_response(
            action='stop',
            status='success',
            message='录音已停止，正在后台处理...',
            data={
                'pause_count': self.session.pause_count,
                'task_id': task.id
            }
        )
        
        logger.info(f"后台任务已启动: {task.id}")
        
        # 8. 清理AudioProcessor
        await self._cleanup_audio_processor()
    
    @database_sync_to_async
    def _refresh_session(self):
        """刷新session对象"""
        self.session.refresh_from_db()

    async def _cleanup_audio_processor(self):
        """安全清理AudioProcessor"""
        if hasattr(self, 'audio_processor') and self.audio_processor is not None:
            try:
                logger.info("清理AudioProcessor")
                await self.audio_processor.cleanup()
            except Exception as e:
                logger.error(f"AudioProcessor清理失败: {e}")
            finally:
                self.audio_processor = None
        else:
            logger.info("AudioProcessor已经为None，跳过清理")

    async def _process_offline_audio_with_cleanup(self):
        """处理离线音频并在完成后清理资源"""
        try:
            # 发送离线处理开始通知
            await self.send_response(
                action='offline_processing',
                status='success',
                message=f'正在处理 {len(self.audio_segments)} 个音频段...',
                data={'stage': 'started'}
            )
            
            # 1. 合并音频段
            merged_audio_path = await self._merge_audio_segments()
            if not merged_audio_path:
                raise Exception("音频合并失败")
            
            # 2. 检查audio_processor是否还存在
            if not hasattr(self, 'audio_processor') or self.audio_processor is None:
                raise Exception("AudioProcessor已被清理，无法进行离线处理")
            
            # 3. 执行说话人分离和转录
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                self._process_merged_audio,
                merged_audio_path
            )
            
            # 4. 发送处理完成通知
            await self.send_response(
                action='offline_processing',
                status='success',
                message='离线处理完成',
                data={'stage': 'completed', 'result': result}
            )
            
            logger.info("离线处理完成")
            
        except Exception as e:
            logger.error(f"离线处理失败: {e}")
            try:
                await self.send_response(
                    action='offline_processing',
                    status='error',
                    message=f"离线处理失败: {str(e)}",
                    code='OFFLINE_PROCESSING_FAILED'
                )
            except Exception:
                pass  # 连接可能已关闭
        finally:
            # 5. 处理完成后才清理AudioProcessor
            await self._cleanup_audio_processor()
            
            # 6. 延迟关闭连接，给前端时间处理结果
            await asyncio.sleep(2)
            try:
                await self.close()
            except Exception:
                pass  # 连接可能已关闭

    async def handle_audio_data(self, audio_bytes):
        """音频数据写盘"""
        if not self.transcription_active or self.is_paused:
            if self.is_paused:
                logger.debug("处于暂停状态，忽略音频数据")
            return
        
        try:
            # 1. 实时写入磁盘
            if self.audio_file_handle is None:
                # ← 修复：根据文件是否存在选择打开模式
                # 如果文件存在（恢复录音），用追加模式；否则用写入模式
                mode = 'ab' if os.path.exists(self.temp_audio_file) else 'wb'
                self.audio_file_handle = open(self.temp_audio_file, mode)
                logger.debug(f"音频文件已打开 (mode={mode}, path={self.temp_audio_file})")
            
            self.audio_file_handle.write(audio_bytes)
            
            # 2. 同时传递给AudioProcessor进行实时转写
            if hasattr(self, 'audio_processor') and self.audio_processor:
                await self.audio_processor.process_audio(audio_bytes)
                
        except Exception as e:
            logger.error(f"音频数据处理异常: {e}")
            # ← 添加：通知前端错误
            await self.send_response(
                action='audio',
                status='error',
                message=f"音频数据处理异常: {str(e)}",
                code='AUDIO_PROCESSING_ERROR'
            )

    async def _handle_results(self):
        """处理AudioProcessor的结果流"""
        try:
            async for result in self.results_generator:
                # 检查连接状态
                if not self.transcription_active or not self.connected:
                    logger.info("转录已停止或连接已关闭，停止处理结果")
                    # 正确关闭生成器
                    await self.results_generator.aclose()
                    break
                    
                if result.get('status') == 'error':
                    await self.send_response(
                        action='processing',
                        status='error',
                        message=result.get('message', '音频处理错误'),
                        code='PROCESSING_ERROR'
                    )
                else:
                    # 可以发送状态更新给前端
                    await self.send_response(
                        action='processing',
                        status='success',
                        message='',
                        data=result
                    )
        except asyncio.CancelledError:
            logger.info("结果处理任务被取消")
            # 确保在任务取消时也关闭生成器
            await self.results_generator.aclose()
            raise  # 重新抛出取消异常
        except Exception as e:
            logger.error(f"结果处理失败: {e}")
            # 确保在发生错误时也关闭生成器
            try:
                await self.results_generator.aclose()
            except Exception as close_error:
                logger.error(f"关闭结果生成器时发生错误: {close_error}")
        finally:
            # 确保在所有情况下都尝试关闭生成器
            try:
                if hasattr(self, 'results_generator'):
                    await self.results_generator.aclose()
            except Exception as e:
                logger.error(f"清理结果生成器时发生错误: {e}")

    async def _handle_transcription_result(self, result: dict):
        """处理转录结果回调"""
        try:
            await self.send_transcription_result(result)
        except Exception as e:
            logger.error(f"发送转录结果失败: {e}")

    async def _handle_audio_error(self, error_msg: str):
        """处理音频错误回调"""
        await self.send_response(
            action='audio',
            status='error',
            message=error_msg,
            code='AUDIO_ERROR'
        ) 
    
    def _transcribe_audio_chunk(self, audio_file):
        """转录音频块 - 同步方法在线程池中执行"""
        try:
            # 使用流式服务转录
            results = list(self.streaming_service.stream_recognize_file(audio_file))
            if results:
                return {
                    'text': results[-1],  # 取最后一个结果
                    'confidence': 0.95,
                    'speaker_id': 'speaker_1',  # 实时转录暂不分离说话人
                    'timestamp': datetime.datetime.now().isoformat(),
                    'is_final': False
                }
        except Exception as e:
            logger.error(f"音频块转录失败: {e}")
        return None
    
    async def _merge_audio_segments(self):
        """合并音频段"""
        logger.info(f'开始合并音频段...')
        try:
            # 调试信息：检查音频段数量和类型
            logger.info(f'音频段检查 - 数量: {len(self.audio_segments) if self.audio_segments else 0}')
            logger.info(f'音频段检查 - 类型: {type(self.audio_segments)}')
            
            if not self.audio_segments:
                logger.warning('没有音频段可合并，返回 None')
                return None
            
            # 调试信息：检查每个音频段的大小
            total_size = 0
            for i, segment in enumerate(self.audio_segments):
                segment_size = len(segment) if segment else 0
                total_size += segment_size
                logger.info(f'音频段 {i}: 大小 {segment_size} 字节, 类型: {type(segment)}')
            
            logger.info(f'总音频数据大小: {total_size} 字节')
            
            # 调试信息：检查临时目录
            logger.info(f'临时目录: {self.temp_dir}')
            logger.info(f'临时目录是否存在: {os.path.exists(self.temp_dir)}')
            
            merged_path = os.path.join(self.temp_dir, f'merged_audio_{self.session_id}.webm')
            logger.info(f'合并音频文件路径: {merged_path}')
            
            # 简单合并：直接连接字节
            bytes_written = 0
            with open(merged_path, 'wb') as outfile:
                for i, segment in enumerate(self.audio_segments):
                    if segment:
                        segment_bytes = len(segment)
                        outfile.write(segment)
                        bytes_written += segment_bytes
                        logger.debug(f'写入音频段 {i}: {segment_bytes} 字节')
                    else:
                        logger.warning(f'音频段 {i} 为空，跳过')
            
            logger.info(f'合并完成，总共写入: {bytes_written} 字节')
            
            # 验证合并后的文件
            if os.path.exists(merged_path):
                file_size = os.path.getsize(merged_path)
                logger.info(f'合并文件创建成功: {merged_path}, 文件大小: {file_size} 字节')
                
                if file_size == 0:
                    logger.error('合并文件大小为0，可能合并失败')
                    return None
                    
                return merged_path
            else:
                logger.error(f'合并文件未创建: {merged_path}')
                return None
            
        except Exception as e:
            logger.error(f"音频合并失败: {e}")
            logger.error(f"错误详情: {str(e)}")
            import traceback
            logger.error(f"堆栈跟踪: {traceback.format_exc()}")
            return None
    
    def _process_merged_audio(self, audio_path):
        """处理合并后的音频 - 同步方法"""
        logger.info(f'开始处理合并后的音频：{audio_path}')
        try:
            # 检查audio_processor是否存在
            if not hasattr(self, 'audio_processor') or self.audio_processor is None:
                raise Exception("AudioProcessor不可用")

            # 检查音频文件是否存在
            if not os.path.exists(audio_path):
                raise Exception(f"音频文件不存在: {audio_path}")
                
            # 执行说话人分离和转录
            result = self.audio_processor.separate_speakers(
                media_path=audio_path,
                output_dir=self.temp_dir,
                merge_threshold=10,
                save_audio_segments=True,
                save_merged_audio=True
            )

            logger.info(f'音频转录结果: {result}')

            if not result or not result.get('success', False):
                error_msg = result.get('message', '未知错误') if result else '处理失败，无返回结果'
                logger.error(f'说话人分离失败: {error_msg}')
            
            # 将处理后的音频复制到可访问的位置
            processed_audio_name = f'merged_audio_{self.session_id}.wav'
            processed_audio_path = os.path.join(self.temp_dir, processed_audio_name)
            
            # 如果结果包含处理后的音频文件，复制它
            if result and 'output_file' in result:
                import shutil
                logger.info(f'复制音频文件: {result["output_file"]} -> {processed_audio_path}')
                shutil.copy2(result['output_file'], processed_audio_path)
            
            return {
                'audio_url': f'/media/processed/{self.session_id}/{processed_audio_name}',
                'speakers_count': len(result.get('speakers', [])) if result else 0,
                'transcription': result.get('transcription', '') if result else '',
                'speakers_info': result.get('speakers', []) if result else []
            }
            
        except Exception as e:
            logger.error(f"音频处理失败: {e}")
            return {
                'audio_url': None,
                'speakers_count': 0,
                'transcription': f'处理失败: {str(e)}',
                'speakers_info': []
            }
    
    async def cleanup_resources(self):
        """清理资源"""
        try:
            # 清理临时目录
            import shutil
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            logger.error(f"资源清理失败: {e}")
    
    async def stop_transcription_session(self):
        """停止转录会话"""
        self.transcription_active = False  # ← 清理状态
        self.meeting_id = self.session_id
        
        await self.send_response(
            action='stop',
            status='success',
            message='转录会话已停止',
            data={'timestamp': datetime.datetime.now().isoformat()}
        )
        
        logger.info(f"会话 {self.session_id} 停止转录")
    
    async def send_transcription_result(self, result):
        """发送转录结果"""
        await self.send_response(
            action='transcription',
            status='success',
            message='',
            data=result
        )
    
    async def send_response(self, action, status, message, code=None, data=None):
        """发送统一格式的响应消息"""
        if not self.connected:
            logger.warning(f"尝试在关闭的连接上发送消息: {message}")
            return
        try:
            response = {
                'type': 'response',
                'action': action,
                'status': status,
                'message': message,
                'session_id': getattr(self, 'session_id', None),
                'data': data or {}
            }
            if code:
                response['code'] = code
            await self.send(text_data=json.dumps(response))
        except Exception as e:
            logger.error(f"发送响应消息失败: {e}")

    async def send_error(self, message, action='general', code=None):
        """发送错误消息（仅用于服务错误）"""
        await self.send_response(action=action, status='error', message=message, code=code)
    
    @database_sync_to_async
    def get_meeting(self, meeting_id):
        """获取会议信息"""
        try:
            return Meeting.objects.get(id=meeting_id)
        except Meeting.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_or_create_session(self):
        """获取或创建实时录音会话"""
        meeting = Meeting.objects.get(id=self.meeting_id)
                
        can_start, message = meeting.can_start_realtime_recording()
        if not can_start:
           raise ValueError(message)           
        
     # ← 使用事务锁防止并发问题
        with transaction.atomic():
            # 使用 select_for_update 锁定会议记录
            meeting = Meeting.objects.select_for_update().get(id=self.meeting_id)
            
            # 尝试获取现有会话
            try:
                session = RealtimeRecordingSession.objects.select_for_update().get(meeting=meeting)
                
                # 如果会话已完成或失败，创建新会话
                if session.recording_status in [5, 6]:
                    session.delete()
                    raise RealtimeRecordingSession.DoesNotExist
                        
            except RealtimeRecordingSession.DoesNotExist:
                # 创建新会话
                session_id = str(meeting.id)
                session = RealtimeRecordingSession.objects.create(
                    meeting=meeting,
                    session_id=session_id,
                    recording_status=0  # 未开始
                )
        
        return session
    
    @database_sync_to_async
    def _update_session_audio_path(self, path):
        """更新会话的音频文件路径"""
        self.session.temp_audio_path = path
        self.session.save(update_fields=['temp_audio_path'])
    
    @database_sync_to_async
    def _update_session_status(self, status):
        """更新会话状态"""
        self.session.recording_status = status
        self.session.save(update_fields=['recording_status', 'update_datetime'])
    
    @database_sync_to_async
    def _session_start_recording(self):
        """数据库：开始录音"""
        self.session.start_recording()
    
    @database_sync_to_async
    def _session_pause_recording(self):
        """数据库：暂停录音"""
        self.session.pause_recording()
    
    @database_sync_to_async
    def _session_resume_recording(self):
        """数据库：恢复录音"""
        self.session.resume_recording()
    
    @database_sync_to_async
    def _session_stop_recording(self, task_id=None):
        """数据库：停止录音"""
        self.session.stop_recording(task_id=task_id)

    @database_sync_to_async
    def _session_mark_failed(self, error_message):
        """数据库：标记为失败"""
        self.session.mark_failed(error_message)
    
    @database_sync_to_async
    def _session_update_audio_info(self):
        """数据库：更新音频信息"""
        self.session.update_audio_info()

    @database_sync_to_async
    def _check_can_start(self):
        """检查是否可以开始"""
        self.session.refresh_from_db()
        return self.session.can_start_recording()
    
    @database_sync_to_async
    def _check_can_pause(self):
        """检查是否可以暂停"""
        self.session.refresh_from_db()
        return self.session.can_pause_recording()
    
    @database_sync_to_async
    def _check_can_resume(self):
        """检查是否可以恢复"""
        self.session.refresh_from_db()
        return self.session.can_resume_recording()
    
    @database_sync_to_async
    def _check_can_stop(self):
        """检查是否可以停止"""
        self.session.refresh_from_db()
        return self.session.can_stop_recording()