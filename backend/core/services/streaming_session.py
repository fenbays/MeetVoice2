"""
流式转录会话管理 - 每个WebSocket连接一个独立会话

设计原则（Linus式）：
1. 一个会话 = 一个FFmpeg进程 + 一组任务
2. 生命周期清晰：init -> start -> process -> stop -> cleanup
3. 资源所有权明确：谁创建谁负责清理
4. 无状态机复杂性：要么运行要么停止
"""
import asyncio
import logging
import numpy as np
from time import time
from typing import Optional, Callable, AsyncIterator
from core.utils.ffmpeg_manager import FFmpegAudioManager, FFmpegState
from core.services.streaming_speech_service import StreamingSpeechService

logger = logging.getLogger(__name__)


class StreamingSession:
    """单个流式转录会话 - 简洁、独立、可并发"""
    
    def __init__(
        self,
        session_id: str,
        speech_service: StreamingSpeechService,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration: float = 5.0
    ):
        self.session_id = session_id
        self.speech_service = speech_service
        
        # 音频参数
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration = chunk_duration
        
        # 计算缓冲区参数
        self.bytes_per_sample = 2  # 16-bit PCM
        self.samples_per_chunk = int(sample_rate * chunk_duration)
        self.bytes_per_chunk = self.samples_per_chunk * self.bytes_per_sample * channels
        self.max_buffer_size = self.bytes_per_chunk * 10
        
        # 核心组件
        self.ffmpeg = FFmpegAudioManager(sample_rate=sample_rate, channels=channels)
        self.pcm_buffer = bytearray()
        self.transcription_queue = asyncio.Queue()
        
        # 状态标志 - 简单明了
        self.running = False
        self.stopping = False
        
        # 异步任务
        self.tasks = []
        
        # 回调函数
        self.on_transcription: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        
        # 设置FFmpeg错误回调
        async def handle_ffmpeg_error(error_type: str):
            logger.error(f"[{session_id}] FFmpeg error: {error_type}")
            if self.on_error:
                await self.on_error(f"FFmpeg错误: {error_type}")
        
        self.ffmpeg.on_error_callback = handle_ffmpeg_error
    
    async def start(self) -> bool:
        """启动会话"""
        if self.running:
            logger.warning(f"[{self.session_id}] Session already running")
            return True
        
        logger.info(f"[{self.session_id}] Starting streaming session...")
        
        # 1. 启动FFmpeg
        success = await self.ffmpeg.start()
        if not success:
            logger.error(f"[{self.session_id}] Failed to start FFmpeg")
            return False
        
        # 2. 标记运行状态
        self.running = True
        self.stopping = False
        
        # 3. 创建处理任务
        self.tasks = [
            asyncio.create_task(self._ffmpeg_reader()),
            asyncio.create_task(self._transcription_processor()),
        ]
        
        logger.info(f"[{self.session_id}] Session started successfully")
        return True
    
    async def process_audio(self, audio_bytes: bytes) -> bool:
        """处理音频数据 - 简单直接"""
        if not audio_bytes:
            logger.info(f"[{self.session_id}] Empty audio, stopping...")
            await self.stop()
            return True
        
        if self.stopping or not self.running:
            logger.warning(f"[{self.session_id}] Session not active, ignoring audio")
            return False
        
        # 健康检查
        if not await self.ffmpeg.health_check():
            logger.error(f"[{self.session_id}] FFmpeg health check failed")
            return False
        
        # 写入音频数据
        success = await self.ffmpeg.write_data(audio_bytes)
        if not success:
            logger.error(f"[{self.session_id}] Failed to write audio data")
        
        return success
    
    async def stop(self):
        """停止会话"""
        if not self.running:
            return
        
        logger.info(f"[{self.session_id}] Stopping session...")
        self.stopping = True
        self.running = False
        
        # 停止FFmpeg
        await self.ffmpeg.stop()
        
        # 等待任务完成
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        logger.info(f"[{self.session_id}] Session stopped")
    
    async def cleanup(self):
        """清理资源"""
        logger.info(f"[{self.session_id}] Cleaning up session...")
        
        # 确保停止
        await self.stop()
        
        # 取消所有任务
        for task in self.tasks:
            if task and not task.done():
                task.cancel()
        
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        self.tasks.clear()
        self.pcm_buffer.clear()
        
        logger.info(f"[{self.session_id}] Cleanup complete")
    
    async def get_status(self) -> dict:
        """获取会话状态"""
        ffmpeg_state = await self.ffmpeg.get_state()
        return {
            "session_id": self.session_id,
            "running": self.running,
            "stopping": self.stopping,
            "ffmpeg_state": ffmpeg_state.value,
            "buffer_size": len(self.pcm_buffer),
            "queue_size": self.transcription_queue.qsize()
        }
    
    # ==================== 内部处理任务 ====================
    
    async def _ffmpeg_reader(self):
        """FFmpeg输出读取器"""
        logger.info(f"[{self.session_id}] FFmpeg reader started")
        beg = time()
        
        try:
            while self.running and not self.stopping:
                try:
                    # 检查FFmpeg状态
                    state = await self.ffmpeg.get_state()
                    if state not in (FFmpegState.RUNNING, FFmpegState.STARTING):
                        logger.warning(f"[{self.session_id}] FFmpeg state: {state}")
                        if state == FFmpegState.FAILED:
                            break
                        await asyncio.sleep(0.5)
                        continue
                    
                    # 动态缓冲区大小
                    current_time = time()
                    elapsed = max(0.1, current_time - beg)
                    buffer_size = max(int(32000 * elapsed), 4096)
                    beg = current_time
                    
                    # 读取PCM数据
                    chunk = await self.ffmpeg.read_data(buffer_size)
                    
                    if not chunk:
                        if self.stopping:
                            break
                        await asyncio.sleep(0.1)
                        continue
                    
                    # 添加到缓冲区
                    self.pcm_buffer.extend(chunk)
                    
                    # 当有足够数据时处理
                    if len(self.pcm_buffer) >= self.bytes_per_chunk:
                        pcm_chunk = self.pcm_buffer[:self.max_buffer_size]
                        self.pcm_buffer = self.pcm_buffer[self.max_buffer_size:]
                        
                        # 转换为numpy数组
                        audio_array = self._pcm_to_float(pcm_chunk)
                        
                        # 放入转录队列
                        await self.transcription_queue.put(audio_array.copy())
                
                except Exception as e:
                    logger.error(f"[{self.session_id}] FFmpeg reader error: {e}")
                    await asyncio.sleep(1)
        
        except Exception as e:
            logger.error(f"[{self.session_id}] FFmpeg reader crashed: {e}")
        
        finally:
            # 发送结束信号
            await self.transcription_queue.put(None)
            logger.info(f"[{self.session_id}] FFmpeg reader stopped")
    
    async def _transcription_processor(self):
        """转录处理器"""
        logger.info(f"[{self.session_id}] Transcription processor started")
        
        try:
            while True:
                try:
                    # 等待音频数据
                    audio_array = await self.transcription_queue.get()
                    
                    # 检查结束信号
                    if audio_array is None:
                        logger.info(f"[{self.session_id}] Transcription end signal received")
                        self.transcription_queue.task_done()
                        break
                    
                    # 异步转录
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        self._transcribe_chunk,
                        audio_array
                    )
                    
                    # 回调
                    if result and self.on_transcription:
                        await self.on_transcription(result)
                    
                    self.transcription_queue.task_done()
                
                except Exception as e:
                    logger.error(f"[{self.session_id}] Transcription error: {e}")
                    self.transcription_queue.task_done()
        
        except Exception as e:
            logger.error(f"[{self.session_id}] Transcription processor crashed: {e}")
        
        finally:
            logger.info(f"[{self.session_id}] Transcription processor stopped")
    
    def _pcm_to_float(self, pcm_buffer: bytes) -> np.ndarray:
        """PCM转浮点数组"""
        audio_int16 = np.frombuffer(pcm_buffer, dtype=np.int16)
        return audio_int16.astype(np.float32) / 32768.0
    
    def _transcribe_chunk(self, audio_array: np.ndarray) -> Optional[dict]:
        """转录音频块 - 同步方法"""
        try:
            import datetime
            
            results = list(self.speech_service.stream_recognize_chunks(
                audio_chunks=[audio_array],
                sample_rate=self.sample_rate
            ))
            
            if results:
                return {
                    'text': results[-1],
                    'confidence': 0.95,
                    'speaker_id': 'speaker_1',
                    'timestamp': datetime.datetime.now().isoformat(),
                    'is_final': False,
                    'audio_duration': len(audio_array) / self.sample_rate,
                    'session_id': self.session_id
                }
        except Exception as e:
            logger.error(f"[{self.session_id}] Transcription failed: {e}")
        
        return None

