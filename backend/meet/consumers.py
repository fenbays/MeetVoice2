import json
import asyncio
import logging
import datetime
import tempfile
import os
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Meeting, Recording

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
        self.session_id = self.scope['url_route']['kwargs']['meeting_id']
        self.room_group_name = f'transcribe_{self.session_id}'
        
        # 初始化状态和音频处理
        self.transcription_active = False
        self.meeting_id = None
        self.audio_segments = []  # 存储音频段用于最终合并
        
        # 创建基于配置的临时目录
        from django.conf import settings
        temp_base = getattr(settings, 'MEETVOICE_TEMP_DIR', '/tmp/meetvoice')
        os.makedirs(temp_base, exist_ok=True)
        self.temp_dir = os.path.join(temp_base, self.session_id)
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 初始化转录服务（移除原来的各种服务初始化）
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
        logger.info(f"转录会话 {self.session_id} 已连接")
    
    async def disconnect(self, close_code):
        """断开连接"""
        self.connected = False
        logger.info(f"会话 {self.session_id} 断开连接: {close_code}")
        self.transcription_active = False
        
        try:
            # 1. 停止结果处理任务
            if hasattr(self, 'result_handler_task') and not self.result_handler_task.done():
                logger.info("取消结果处理任务")
                self.result_handler_task.cancel()
                try:
                    await self.result_handler_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"结果处理任务取消失败: {e}")
            
            # 2. 确保关闭结果生成器
            if hasattr(self, 'results_generator'):
                try:
                    await self.results_generator.aclose()
                except Exception as e:
                    logger.error(f"关闭结果生成器失败: {e}")
                
            # 3. 安全清理AudioProcessor
            if hasattr(self, 'audio_processor') and self.audio_processor is not None:
                try:
                    logger.info("断开连接时清理AudioProcessor")
                    await self.audio_processor.cleanup()
                except Exception as e:
                    logger.error(f"AudioProcessor清理失败: {e}")
                finally:
                    self.audio_processor = None
            
            # 4. 清理其他资源
            await self.cleanup_resources()
            
            # 5. 离开群组
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
            logger.info(f"转录会话 {self.session_id} 已断开: {close_code}")
        
        except Exception as e:
            logger.error(f"断开连接时发生错误: {e}")
            import traceback
            traceback.print_exc()
            try:
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name
                )
            except Exception:
                pass
    
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
                    await self.send_error("无效的JSON格式")
                except ValueError as e:
                    await self.send_error(str(e))
            elif bytes_data:
                # 音频数据处理
                if not getattr(self, 'transcription_active', False):  # ← 使用getattr安全获取
                    await self.send_error("请先发送start_transcription命令")
                    return
                await self.handle_audio_data(bytes_data)
            else:
                await self.send_error("无效的消息格式")
        except Exception as e:
            logger.error(f"消息处理错误: {e}")
            await self.send_error(str(e))
    
    async def handle_control_message(self, data):
        """处理控制消息"""
        message_type = data.get('type')
        
        if message_type == 'start_transcription':
            self.transcription_active = True
            
         # 启动AudioProcessor任务
            try:
                if not hasattr(self, 'audio_processor') or self.audio_processor is None:
                    logger.error("AudioProcessor未初始化")
                    await self.send_error("音频处理器未初始化")
                    return
                    
                self.results_generator = await self.audio_processor.create_tasks()
                # 启动结果处理任务
                self.result_handler_task = asyncio.create_task(self._handle_results())
            except Exception as e:
                logger.error(f"启动音频处理失败: {e}")
                await self.send_error("音频处理器启动失败")
                return
                
            await self.send(text_data=json.dumps({
                'type': 'transcription_started',
                'session_id': self.session_id
            }))

            # 异步初始化模型和AudioProcessor任务
            asyncio.create_task(self._initialize_streaming_models_and_start())
        
        elif message_type == 'stop_transcription':
            await self._handle_stop_transcription()
                
        elif message_type == 'ping':
            # 心跳
            await self.send(text_data=json.dumps({
                'type': 'pong',
                'timestamp': datetime.datetime.now().isoformat()
            }))
        else:
            await self.send_error(f"不支持的消息类型: {message_type}")

    async def _initialize_streaming_models_and_start(self):
        """异步初始化模型并启动处理任务"""
        try:
            # 1. 发送模型加载开始消息
            await self.send(text_data=json.dumps({
                'type': 'model_loading_started',
                'session_id': self.session_id,
                'message': '正在加载AI模型...'
            }))
            
            # 2. 等待模型准备完成 
            logger.info("开始加载模型...")
            
            # 检查AudioProcessor是否有模型准备方法
            if hasattr(self.audio_processor, 'prepare_models'):
                success = await self.audio_processor.prepare_models()
                if not success:
                    raise Exception("模型准备失败")
            else:
                # 模拟模型加载时间
                await asyncio.sleep(15)
            
            logger.info("模型加载完成")
            
            # 3. 启动AudioProcessor任务
            if not hasattr(self, 'audio_processor') or self.audio_processor is None:
                raise Exception("AudioProcessor未初始化")
                
            self.results_generator = await self.audio_processor.create_tasks()
            # 启动结果处理任务
            self.result_handler_task = asyncio.create_task(self._handle_results())
            
            # 4. 发送模型准备完成消息
            await self.send(text_data=json.dumps({
                'type': 'model_loading_completed',
                'session_id': self.session_id,
                'message': '模型加载完成，可以开始录音'
            }))
            
            logger.info("音频处理系统准备完成")
            
        except Exception as e:
            logger.error(f"模型初始化失败: {e}")
            await self.send(text_data=json.dumps({
                'type': 'model_loading_failed',
                'session_id': self.session_id,
                'error': str(e),
                'message': '模型加载失败，请重试'
            }))

    async def _handle_stop_transcription(self):
        """处理停止转录请求"""
        logger.info(f"开始停止转录会话 {self.session_id}")
        
        # 1. 立即停止接收新的音频数据
        self.transcription_active = False
        
        try:
            # 2. 发送停止确认
            await self.send(text_data=json.dumps({
                'type': 'transcription_stopped',
                'session_id': self.session_id,
                'timestamp': datetime.datetime.now().isoformat()
            }))
            
            # 3. 停止结果处理任务
            if hasattr(self, 'result_handler_task') and not self.result_handler_task.done():
                logger.info("取消结果处理任务")
                self.result_handler_task.cancel()
                try:
                    await self.result_handler_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"结果处理任务取消失败: {e}")
                    
            # 4. 检查音频段并启动离线处理
            logger.info(f"检查音频段: hasattr(audio_segments)={hasattr(self, 'audio_segments')}, "                       
                       f"segments数量={len(self.audio_segments) if hasattr(self, 'audio_segments') else 'N/A'}")
            
            if hasattr(self, 'audio_segments') and self.audio_segments:
                logger.info(f"启动离线处理，共 {len(self.audio_segments)} 个音频段")
                # 直接创建后台任务处理离线音频，避免嵌套调用
                asyncio.create_task(self._process_offline_audio_with_cleanup())
            else:
                logger.info("没有音频段需要处理，直接清理资源")
                await self._cleanup_audio_processor()
                
            logger.info(f"会话 {self.session_id} 停止转录处理完成")
            
        except Exception as e:
            logger.error(f"停止转录处理失败: {e}")
            import traceback
            traceback.print_exc()
            await self._cleanup_audio_processor()
            await self.send_error(f"停止转录失败: {str(e)}")

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
            await self.send(text_data=json.dumps({
                'type': 'offline_processing_started',
                'message': f'正在处理 {len(self.audio_segments)} 个音频段...',
                'session_id': self.session_id
            }))
            
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
            await self.send(text_data=json.dumps({
                'type': 'offline_processing_completed',
                'result': result,
                'session_id': self.session_id
            }))
            
            logger.info("离线处理完成")
            
        except Exception as e:
            logger.error(f"离线处理失败: {e}")
            try:
                await self.send_error(f"离线处理失败: {str(e)}")
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
        """处理音频数据 - 加强安全检查"""
        logger.info(f"收到音频数据，大小: {len(audio_bytes)} 字节，转录状态: {self.transcription_active}")
        
        if not self.transcription_active:
            # 如果转录未激活，直接忽略音频数据，不发送错误
            logger.warning("转录未激活，忽略音频数据")
            return
        
        try:
            # 1. 存储音频段用于最终合并
            if hasattr(self, 'audio_segments'):
                self.audio_segments.append(audio_bytes)
                logger.info(f"音频段已存储，当前总数: {len(self.audio_segments)}")
            else:
                logger.error("audio_segments 属性不存在！")
            
            # 2. 安全检查audio_processor
            if not hasattr(self, 'audio_processor') or self.audio_processor is None:
                logger.warning("AudioProcessor未初始化或已清理，忽略音频数据")
                return
                
            # 3. 直接传递给AudioProcessor
            success = await self.audio_processor.process_audio(audio_bytes)
            if not success:
                logger.error("音频处理失败")
                
        except Exception as e:
            logger.error(f"音频数据处理失败: {e}")
            import traceback
            traceback.print_exc()

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
                    await self.send_error(result.get('message', '音频处理错误'))
                else:
                    # 可以发送状态更新给前端
                    await self.send(text_data=json.dumps({
                        'type': 'processing_status',
                        'data': result
                    }))
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
        await self.send_error(error_msg)
    
    async def real_time_transcribe(self, audio_bytes):
        """实时转录 - 真实实现"""
        try:
            # 将音频数据写入临时文件
            temp_audio_file = os.path.join(self.temp_dir, f'chunk_{len(self.audio_segments)}.webm')
            with open(temp_audio_file, 'wb') as f:
                f.write(audio_bytes)
            
            # 调用流式转录服务
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                self._transcribe_audio_chunk, 
                temp_audio_file
            )
            
            if result:
                await self.send_transcription_result(result)
                
        except Exception as e:
            logger.error(f"实时转录失败: {e}")
    
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
    
    async def start_transcription_session(self, meeting_id):
        """开始转录会话"""
        try:
            # 验证会议存在
            meeting = await self.get_meeting(meeting_id)
            if not meeting:
                await self.send_error("会议不存在")
                return
            
            # 设置状态
            self.transcription_active = True  # ← 设置状态
            self.meeting_id = meeting_id
            
            await self.send(text_data=json.dumps({
                'type': 'transcription_started',
                'session_id': self.session_id,
                'meeting_id': meeting_id,
                'meeting_title': meeting.title,
                'timestamp': datetime.datetime.now().isoformat()
            }))
            
            logger.info(f"会话 {self.session_id} 开始转录，会议ID: {meeting_id}")
            
        except Exception as e:
            logger.error(f"启动转录会话失败: {e}")
            await self.send_error(f"启动失败: {str(e)}")
    
    async def stop_transcription_session(self):
        """停止转录会话"""
        self.transcription_active = False  # ← 清理状态
        self.meeting_id = None
        
        await self.send(text_data=json.dumps({
            'type': 'transcription_stopped',
            'session_id': self.session_id,
            'timestamp': datetime.datetime.now().isoformat()
        }))
        
        logger.info(f"会话 {self.session_id} 停止转录")
    
    async def send_transcription_result(self, result):
        """发送转录结果"""
        await self.send(text_data=json.dumps({
            'type': 'transcription_result',
            'session_id': self.session_id,
            'result': result
        }))
    
    async def send_error(self, message):
        if not self.connected:
            logger.warning(f"尝试在关闭的连接上发送错误消息: {message}")
            return
        try:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': message
            }))
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")
    
    @database_sync_to_async
    def get_meeting(self, meeting_id):
        """获取会议信息"""
        try:
            return Meeting.objects.get(id=meeting_id)
        except Meeting.DoesNotExist:
            return None