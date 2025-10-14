import os
import asyncio
import logging
import numpy as np
from time import time
from typing import AsyncIterator, List, Dict, Optional, Callable
from core.utils.ffmpeg_manager import FFmpegAudioManager, FFmpegState
from conf.model import ModelConfig
from core.services.denoising_service import DenoisingService
from core.utils.model_manager import ModelManager
from core.utils.media_processor import MediaProcessor
from core.services.speech_service import SpeechRecognitionService
from core.services.streaming_speech_service import StreamingSpeechService
from core.services.speaker_separation_service import SpeakerSeparationService

logger = logging.getLogger(__name__)

class AudioProcessor:
    """音频处理业务逻辑"""
    
    def __init__(self, **kwargs):
        self.model_config = ModelConfig()
        self.model_manager = ModelManager(self.model_config)
        self.speech_service = SpeechRecognitionService(self.model_manager)
        self.streaming_service = StreamingSpeechService(self.model_manager)
        self.speaker_service = SpeakerSeparationService(self.model_manager)
        self.denoising_service = DenoisingService(self.model_manager)
        self.temp_files = []  # 用于跟踪临时文件


        # 音频处理配置
        self.sample_rate = 16000
        self.channels =1
        self.chunk_duration = kwargs.get('chunk_duration', 5)
        
        # 计算缓冲区参数
        self.bytes_per_sample = 2  # 16-bit PCM
        self.samples_per_chunk = int(self.sample_rate * self.chunk_duration)
        self.bytes_per_chunk = self.samples_per_chunk * self.bytes_per_sample * self.channels
        self.max_buffer_size = self.bytes_per_chunk * 10  # 最大缓冲10个块
        
        # FFmpeg管理器
        self.ffmpeg_manager = FFmpegAudioManager(
            sample_rate=self.sample_rate,
            channels=self.channels,
        )
        
        # 状态管理 - 简化为3个状态
        # IDLE: 初始化完成，未启动
        # RUNNING: 正在处理音频
        # STOPPED: 已停止，可以被清理
        self._state = "IDLE"
        self._state_lock = asyncio.Lock()
        
        self.pcm_buffer = bytearray()
        self.temp_files = []
        
        # 异步任务管理
        self.transcription_queue = asyncio.Queue()
        self.ffmpeg_reader_task = None
        self.transcription_task = None
        self.watchdog_task = None
        self.all_tasks_for_cleanup = []
        
        # 回调函数（支持sync和async）
        self.on_transcription_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None

    async def create_tasks(self) -> AsyncIterator[dict]:
        """
        创建并启动所有处理任务 - 幂等操作
        
        【Linus原则】：
        1. 已经RUNNING就返回现有的生成器，不创建新任务
        2. 只有IDLE状态才能启动
        3. STOPPED状态说明已经cleanup过，不能重用
        """
        async with self._state_lock:
            if self._state == "RUNNING":
                logger.info("AudioProcessor已在运行，复用现有任务")
                # 返回现有的结果生成器
                return self.results_formatter()
            
            if self._state == "STOPPED":
                logger.error("AudioProcessor已停止，无法重新启动（需要创建新实例）")
                async def error_generator():
                    yield {
                        "status": "error",
                        "message": "处理器已停止，请刷新页面重试",
                        "timestamp": time()
                    }
                return error_generator()
            
            # 只有IDLE状态才继续启动
            if self._state != "IDLE":
                logger.error(f"无效的状态转换: {self._state} -> RUNNING")
                async def error_generator():
                    yield {
                        "status": "error",
                        "message": "音频处理器状态异常",
                        "timestamp": time()
                    }
                return error_generator()
        
        # 启动FFmpeg管理器
        logger.info("启动FFmpeg管理器...")
        success = await self.ffmpeg_manager.start()
        if not success:
            logger.error("FFmpeg管理器启动失败")
            async def error_generator():
                yield {
                    "status": "error",
                    "message": "音频处理器启动失败",
                    "timestamp": time()
                }
            return error_generator()
        
        # 创建所有异步任务
        logger.info("创建处理任务...")
        self.all_tasks_for_cleanup = []
        processing_tasks_for_watchdog = []
        
        self.transcription_task = asyncio.create_task(self.transcription_processor())
        self.all_tasks_for_cleanup.append(self.transcription_task)
        processing_tasks_for_watchdog.append(self.transcription_task)
        
        self.ffmpeg_reader_task = asyncio.create_task(self.ffmpeg_stdout_reader())
        self.all_tasks_for_cleanup.append(self.ffmpeg_reader_task)
        processing_tasks_for_watchdog.append(self.ffmpeg_reader_task)
        
        self.watchdog_task = asyncio.create_task(self.watchdog(processing_tasks_for_watchdog))
        self.all_tasks_for_cleanup.append(self.watchdog_task)
        
        # 状态转换 IDLE -> RUNNING
        async with self._state_lock:
            self._state = "RUNNING"
        
        logger.info("AudioProcessor已启动，状态: RUNNING")
        return self.results_formatter()

    async def process_audio(self, audio_bytes: bytes) -> bool:
        """处理音频数据 - 只在RUNNING状态接受数据"""
        # 检查状态
        async with self._state_lock:
            current_state = self._state
        
        if current_state != "RUNNING":
            logger.warning(f"AudioProcessor状态为{current_state}，拒绝音频数据")
            return False
        
        # 空数据表示结束
        if not audio_bytes:
            logger.info("收到空音频消息，停止FFmpeg输入")
            await self.ffmpeg_manager.stop()
            return True

        # 健康检查
        if not await self.ffmpeg_manager.health_check():
            logger.error("FFmpeg健康检查失败，尝试重启")
            restart_success = await self.ffmpeg_manager.restart()
            if not restart_success:
                logger.error("FFmpeg重启失败")
                return False
            
            # 等待一下让FFmpeg稳定
            await asyncio.sleep(0.1)

        # 写入音频数据
        success = await self.ffmpeg_manager.write_data(audio_bytes)
        if not success:
            ffmpeg_state = await self.ffmpeg_manager.get_state()
            logger.error(f"写入音频数据失败，FFmpeg状态: {ffmpeg_state}")
            
            # 尝试重启一次
            if ffmpeg_state == FFmpegState.FAILED:
                logger.info("尝试重启FFmpeg...")
                restart_success = await self.ffmpeg_manager.restart()
                if restart_success:
                    # 重试写入
                    success = await self.ffmpeg_manager.write_data(audio_bytes)
                    
        return success

    async def ffmpeg_stdout_reader(self):
        """FFmpeg stdout读取器 - 清晰的状态检查"""
        logger.info("开始FFmpeg stdout读取...")
        beg = time()
        
        try:
            while True:
                # 检查状态
                async with self._state_lock:
                    if self._state != "RUNNING":
                        logger.info(f"状态变为{self._state}，停止读取FFmpeg输出")
                        break
                try:
                    # 检查FFmpeg状态
                    state = await self.ffmpeg_manager.get_state()
                    if state == FFmpegState.FAILED:
                        logger.error("FFmpeg处于失败状态")
                        break
                    elif state == FFmpegState.STOPPED:
                        logger.info("FFmpeg已停止")
                        break
                    elif state != FFmpegState.RUNNING:
                        logger.warning(f"FFmpeg状态: {state}，等待中...")
                        await asyncio.sleep(0.5)
                        continue
                    
                    # 计算动态缓冲区大小
                    current_time = time()
                    elapsed_time = max(0.1, current_time - beg)
                    buffer_size = max(int(32000 * elapsed_time), 4096)
                    beg = current_time

                    # 从FFmpeg读取PCM数据
                    chunk = await self.ffmpeg_manager.read_data(buffer_size)
                    
                    if not chunk:
                        # 无数据时短暂等待
                        await asyncio.sleep(0.1)
                        continue
                    
                    # 添加到PCM缓冲区
                    self.pcm_buffer.extend(chunk)

                    # 当有足够数据时处理
                    if len(self.pcm_buffer) >= self.bytes_per_chunk:
                        if len(self.pcm_buffer) > self.max_buffer_size:
                            logger.warning(f"PCM缓冲区过大: {len(self.pcm_buffer) / self.bytes_per_chunk:.1f}块")

                        # 提取音频块并转换
                        pcm_chunk = self.pcm_buffer[:self.max_buffer_size]
                        self.pcm_buffer = self.pcm_buffer[self.max_buffer_size:]
                        
                        # 转换为numpy数组
                        audio_array = self.convert_pcm_to_float(pcm_chunk)
                        
                        # 放入转录队列
                        await self.transcription_queue.put(audio_array.copy())
                        
                except Exception as e:
                    logger.error(f"FFmpeg读取错误: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"FFmpeg stdout读取任务异常: {e}")
        finally:
            # 发送结束信号
            await self.transcription_queue.put(None)
            logger.info("FFmpeg stdout读取任务结束")

    async def transcription_processor(self):
        """转录处理器"""
        logger.info("开始转录处理...")
        
        try:
            while True:
                try:
                    # 等待音频数据
                    audio_array = await self.transcription_queue.get()
                    
                    # 检查结束信号
                    if audio_array is None:
                        logger.info("收到转录结束信号")
                        self.transcription_queue.task_done()
                        break
                    
                    # 异步转录处理
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, 
                        self._transcribe_audio_array, 
                        audio_array
                    )
                    
                    # 调用回调（支持sync和async）
                    if result and self.on_transcription_callback:
                        if asyncio.iscoroutinefunction(self.on_transcription_callback):
                            await self.on_transcription_callback(result)
                        else:
                            self.on_transcription_callback(result)
                    
                    self.transcription_queue.task_done()
                    
                except Exception as e:
                    logger.error(f"转录处理错误: {e}")
                    self.transcription_queue.task_done()
                    
        except Exception as e:
            logger.error(f"转录处理器异常: {e}")
        finally:
            logger.info("转录处理器结束")

    async def watchdog(self, tasks_to_monitor):
        """监控关键处理任务的健康状态"""
        logger.info("启动任务监控...")
        
        try:
            while True:
                # 检查状态
                async with self._state_lock:
                    if self._state != "RUNNING":
                        logger.info(f"状态变为{self._state}，停止监控")
                        break
                try:
                    # 检查任务状态
                    for task in tasks_to_monitor:
                        if task.done():
                            exception = task.exception()
                            if exception:
                                logger.error(f"任务异常退出: {exception}")
                            else:
                                logger.warning("任务意外完成")
                    
                    # 检查FFmpeg状态
                    ffmpeg_state = await self.ffmpeg_manager.get_state()
                    if ffmpeg_state == FFmpegState.FAILED:
                        logger.error("FFmpeg处于失败状态")
                    elif ffmpeg_state == FFmpegState.STOPPED:
                        # FFmpeg意外停止，尝试重启
                        async with self._state_lock:
                            if self._state == "RUNNING":
                                logger.warning("FFmpeg意外停止，尝试重启")
                                await self.ffmpeg_manager.restart()
                    
                    await asyncio.sleep(5)  # 每5秒检查一次
                    
                except asyncio.CancelledError:
                    logger.info("监控任务被取消")
                    break
                except Exception as e:
                    logger.error(f"监控任务错误: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"监控任务异常: {e}")
        finally:
            logger.info("任务监控结束")

    async def results_formatter(self) -> AsyncIterator[dict]:
        """结果格式化器 - 生成转录结果流"""
        logger.info("启动结果格式化器...")
        
        try:
            while True:
                # 检查状态
                async with self._state_lock:
                    current_state = self._state
                
                if current_state != "RUNNING":
                    logger.info(f"状态变为{current_state}，停止结果格式化")
                    break
                try:
                    # 发送状态更新
                    yield {
                        "status": "processing",
                        "timestamp": time(),
                        "buffer_size": len(self.pcm_buffer)
                    }
                    
                    await asyncio.sleep(0.5)  # 每500ms发送一次状态
                    
                except Exception as e:
                    logger.error(f"结果格式化器错误: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"结果格式化器异常: {e}")
        finally:
            # 发送最终状态
            yield {
                "status": "finished",
                "timestamp": time()
            }
            logger.info("结果格式化器结束")

    async def cleanup(self):
        """
        清理资源 - 幂等操作
        
        【Linus原则】：cleanup可以被多次调用，不会出错
        """
        async with self._state_lock:
            if self._state == "STOPPED":
                logger.info("AudioProcessor已经清理过，跳过")
                return
            
            logger.info(f"开始清理AudioProcessor（当前状态: {self._state}）...")
            # 状态转换 -> STOPPED
            self._state = "STOPPED"
        
        # 取消所有任务
        for task in self.all_tasks_for_cleanup:
            if task and not task.done():
                task.cancel()
        
        # 等待任务完成
        created_tasks = [t for t in self.all_tasks_for_cleanup if t]
        if created_tasks:
            await asyncio.gather(*created_tasks, return_exceptions=True)
        logger.info("所有处理任务已取消或完成")
        
        # 停止FFmpeg管理器
        await self.ffmpeg_manager.stop()
        logger.info("FFmpeg管理器已停止")
        
        # 清理临时文件
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")
        
        logger.info("AudioProcessor清理完成，状态: STOPPED")

    def convert_pcm_to_float(self, pcm_buffer) -> np.ndarray:
        """将PCM缓冲区转换为标准化的NumPy数组（接受bytes或bytearray）"""
        audio_int16 = np.frombuffer(pcm_buffer, dtype=np.int16)
        return audio_int16.astype(np.float32) / 32768.0

    def _transcribe_audio_array(self, audio_array: np.ndarray) -> Optional[dict]:
        """转录音频数组 - 同步方法"""
        try:
            import datetime
            
            # 直接使用音频数组进行转录
            results = list(self.streaming_service.stream_recognize_chunks(
                audio_chunks=[audio_array],  # 将单个数组作为一个块传入
                sample_rate=self.sample_rate  # 直接使用当前的采样率
            ))
            
            if results:
                return {
                    'text': results[-1],
                    'confidence': 0.95,
                    'speaker_id': 'speaker_1',
                    'timestamp': datetime.datetime.now().isoformat(),
                    'is_final': False,
                    'audio_duration': len(audio_array) / self.sample_rate
                }
        except Exception as e:
            logger.error(f"音频转录失败: {e}")
            return None

    def set_transcription_callback(self, callback: Callable[[dict], None]):
        """设置转录结果回调"""
        self.on_transcription_callback = callback

    def set_error_callback(self, callback: Callable[[str], None]):
        """设置错误回调"""
        self.on_error_callback = callback
    
    def _prepare_audio_file(self, media_path: str) -> Optional[str]:
        """
        准备音频文件，如果是视频文件则提取音频
        
        Args:
            media_path: 媒体文件路径
            
        Returns:
            音频文件路径，失败返回None
        """
        if not os.path.exists(media_path):
            print(f"❌ 文件不存在: {media_path}")
            return None
        
        # 获取媒体信息
        media_info = MediaProcessor.get_media_info(media_path)
        if not media_info:
            print(f"❌ 无法获取媒体文件信息: {media_path}")
            return None
        
        print(f"📁 文件信息:")
        print(f"   类型: {media_info['type']}")
        print(f"   时长: {media_info['duration']:.2f}秒")
        print(f"   大小: {media_info['file_size'] / 1024 / 1024:.2f}MB")
        
        if media_info['type'] == 'audio':
            # 音频文件直接返回
            print(f"🎵 音频文件，直接处理")
            return media_path
        elif media_info['type'] == 'video':
            # 视频文件需要提取音频
            if not media_info['has_audio']:
                print(f"❌ 视频文件不包含音频流")
                return None
            
            print(f"🎬 视频文件，包含 {media_info['audio_streams']} 个音频流")
            
            # 提取音频
            audio_path = MediaProcessor.extract_audio_from_video(media_path)
            if audio_path:
                self.temp_files.append(audio_path)  # 记录临时文件
                print(f"✅ 音频提取完成: {audio_path}")
                return audio_path
            else:
                print(f"❌ 音频提取失败")
                return None
        else:
            print(f"❌ 不支持的文件类型")
            return None

    def _preprocess_audio(self, media_path: str, enable_denoising: bool = False) -> Optional[str]:
        """
        统一的音频预处理：格式转换 + 可选FRCRN降噪
        
        Args:
            media_path: 媒体文件路径
            enable_denoising: 是否启用降噪
            
        Returns:
            预处理后的音频文件路径，失败返回None
        """
        # 1. 现有的媒体文件准备逻辑
        audio_path = self._prepare_audio_file(media_path)
        if not audio_path:
            return None
        
        # 2. 可选的FRCRN模型降噪处理
        if enable_denoising:
            if self.denoising_service.is_available():
                model_info = self.denoising_service.get_model_info()
                print(f"🔧 启用降噪: {model_info['model']} ({model_info['type']})")
                
                denoised_path = self.denoising_service.denoise(audio_path)
                if denoised_path and denoised_path != audio_path:
                    print(f"✅ 降噪处理完成")
                    return denoised_path
                else:
                    print("⚠️ 降噪处理失败或无效果，使用原始音频")
                    return audio_path
            else:
                print("⚠️ 降噪服务不可用，使用原始音频")
                return audio_path
        
        return audio_path

    def process_single_audio(self, 
                           media_path: str, 
                           language: str = "auto",
                           streaming: bool = False) -> Optional[str]:
        """处理单个媒体文件（音频或视频）- 语音识别"""
        print(f"\n=== 处理媒体文件 ===")
        print(f"文件: {media_path}")
        print(f"语言: {language}")
        print(f"模式: {'流式' if streaming else '离线'}")
        
        # 准备音频文件
        audio_path = self._prepare_audio_file(media_path)
        if not audio_path:
            return None
        
        try:
            if streaming:
                # 流式识别
                print("🔄 开始流式识别...")
                results = []
                for result in self.streaming_service.stream_recognize_file(audio_path):
                    results.append(result)
                
                # 合并所有结果
                if results:
                    full_text = " ".join([r.split(": ", 1)[1] for r in results if ": " in r])
                    print(f"\n✅ 完整识别结果: {full_text}")
                    return full_text
                return None
            else:
                # 离线识别
                print("🔄 开始离线识别...")
                result = self.speech_service.recognize_file(
                    audio_path, 
                    language=language
                )
                
                if result:
                    print(f"✅ 识别结果: {result}")
                    return result
                return None
                
        except Exception as e:
            print(f"❌ 识别失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def separate_speakers(self, 
                         media_path: str, 
                         output_dir: str,
                         merge_threshold: int = 10,
                         save_audio_segments: bool = True,
                         save_merged_audio: bool = True,
                         hotwords: Optional[List[str]] = None,
                         progress_callback: Optional[Callable] = None) -> Dict:
        """
        执行说话人分离
        
        Args:
            media_path: 媒体文件路径
            output_dir: 输出目录
            merge_threshold: 合并相邻相同说话人的字数阈值
            save_audio_segments: 是否保存音频片段
            save_merged_audio: 是否保存合并的音频
            hotwords: 热词列表
            progress_callback: 进度回调函数
            
        Returns:
            分离结果字典
        """
        print(f"\n=== 说话人分离 ===")
        print(f"文件: {media_path}")
        print(f"输出目录: {output_dir}")
        print(f"合并阈值: {merge_threshold}")
        
        # 准备音频文件
        audio_path = self._prepare_audio_file(media_path)
        if not audio_path:
            return {'success': False, 'message': '音频文件准备失败'}
        
        try:
            # 设置热词
            if hotwords:
                self.speaker_service.set_hotwords(hotwords_list=hotwords)
            
            # 执行说话人分离
            result = self.speaker_service.separate_speakers(
                audio_path, 
                merge_threshold=merge_threshold,
                progress_callback=progress_callback
            )
            
            if result['success']:
                # 保存结果
                saved_paths = self.speaker_service.save_separation_results(
                    result, 
                    output_dir,
                    save_audio_segments=save_audio_segments,
                    save_merged_audio=save_merged_audio
                )
                result['saved_paths'] = saved_paths
                
                print(f"✅ 说话人分离完成")
                print(f"📊 结果:")
                print(f"   检测到说话人: {len(result['speakers'])} 个")
                print(f"   总时长: {result['processing_time']:.2f}秒")
                print(f"   保存路径: {saved_paths['base_dir']}")
            
            return result
            
        except Exception as e:
            error_msg = f"说话人分离失败: {e}"
            print(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'message': error_msg}
    
    def batch_separate_speakers(self, 
                              media_paths: List[str], 
                              output_dir: str,
                              merge_threshold: int = 10,
                              save_audio_segments: bool = True,
                              save_merged_audio: bool = True,
                              hotwords: Optional[List[str]] = None,
                              progress_callback: Optional[Callable] = None) -> List[Dict]:
        """
        批量说话人分离
        
        Args:
            media_paths: 媒体文件路径列表
            output_dir: 输出目录
            merge_threshold: 合并阈值
            save_audio_segments: 是否保存音频片段
            save_merged_audio: 是否保存合并的音频
            hotwords: 热词列表
            progress_callback: 进度回调函数
            
        Returns:
            处理结果列表
        """
        print(f"\n=== 批量说话人分离 ===")
        print(f"文件数量: {len(media_paths)}")
        print(f"输出目录: {output_dir}")
        
        # 准备音频文件列表
        audio_files = []
        for media_path in media_paths:
            audio_path = self._prepare_audio_file(media_path)
            if audio_path:
                audio_files.append(audio_path)
            else:
                print(f"⚠️ 跳过无效文件: {media_path}")
        
        if not audio_files:
            return [{'success': False, 'message': '没有有效的音频文件'}]
        
        try:
            # 设置热词
            if hotwords:
                self.speaker_service.set_hotwords(hotwords_list=hotwords)
            
            # 执行批量处理
            results = self.speaker_service.batch_separate_speakers(
                audio_files,
                output_dir,
                merge_threshold=merge_threshold,
                progress_callback=progress_callback
            )
            
            # 统计结果
            success_count = sum(1 for r in results if r.get('success', False))
            print(f"✅ 批量处理完成: {success_count}/{len(results)} 个文件成功")
            
            return results
            
        except Exception as e:
            error_msg = f"批量处理失败: {e}"
            print(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return [{'success': False, 'message': error_msg}]
    
    def compare_modes(self, media_path: str, language: str = "auto") -> Dict:
        """比较离线和流式模式"""
        print(f"\n=== 模式对比 ===")
        print(f"文件: {media_path}")
        
        results = {
            'offline': None,
            'streaming': None,
            'offline_time': 0,
            'streaming_time': 0
        }
        
        import time
        
        # 离线模式
        print("\n🔄 测试离线模式...")
        start_time = time.time()
        results['offline'] = self.process_single_audio(media_path, language, streaming=False)
        results['offline_time'] = time.time() - start_time
        
        # 流式模式
        print("\n🔄 测试流式模式...")
        start_time = time.time()
        results['streaming'] = self.process_single_audio(media_path, language, streaming=True)
        results['streaming_time'] = time.time() - start_time
        
        # 显示对比结果
        print(f"\n📊 对比结果:")
        print(f"离线模式: {results['offline']} (耗时: {results['offline_time']:.2f}秒)")
        print(f"流式模式: {results['streaming']} (耗时: {results['streaming_time']:.2f}秒)")
        
        return results
    
    def analyze_audio_with_all_features(self, 
                                      media_path: str, 
                                      output_dir: str,
                                      language: str = "auto",
                                      merge_threshold: int = 10,
                                      hotwords: Optional[List[str]] = None,
                                      enable_denoising: bool = False) -> Dict:
        """
        分析音频：语音识别 + 说话人分离
        
        Args:
            media_path: 媒体文件路径
            output_dir: 输出目录
            language: 语言设置
            merge_threshold: 合并阈值
            hotwords: 热词列表
            
        Returns:
            完整分析结果
        """
        print(f"\n=== 完整音频分析 ===")
        print(f"文件: {media_path}")
        
        results = {
            'speech_recognition': None,
            'speaker_separation': None,
            'success': False,
            'message': ''
        }
        
        try:
             # 记录降噪模型信息
            if enable_denoising:
                results['denoising_model'] = self.denoising_service.get_model_info()

            # 1. 统一音频预处理（包含可选FRCRN降噪）
            processed_audio = self._preprocess_audio(media_path, enable_denoising)
            if not processed_audio:
                results['message'] = '音频预处理失败'
                return results

            # 1. 执行语音识别
            print("\n🔄 执行语音识别...")
            speech_result = self.process_single_audio(processed_audio, language)
            results['speech_recognition'] = speech_result
            
            # 2. 执行说话人分离
            print("\n🔄 执行说话人分离...")
            speaker_result = self.separate_speakers(
                processed_audio, 
                output_dir,
                merge_threshold=merge_threshold,
                hotwords=hotwords
            )
            results['speaker_separation'] = speaker_result
            
            if speech_result or speaker_result.get('success', False):
                results['success'] = True
                results['message'] = '音频分析完成'
            else:
                results['message'] = '音频分析失败'
            
            return results
            
        except Exception as e:
            error_msg = f"完整分析失败: {e}"
            print(f"❌ {error_msg}")
            results['message'] = error_msg
            return results
    
    async def prepare_streaming_models(self) -> bool:
        """准备模型 - 异步加载"""
        try:
            logger.info("开始准备流式转录模型...")
            
            if hasattr(self, 'streaming_service') and self.streaming_service:
                success = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    self.streaming_service._prepare_streaming_model
                )
                if not success:
                    logger.error("流式模型准备失败")
                    return False
            
            # 模拟额外的准备时间
            await asyncio.sleep(1)
            
            logger.info("模型准备完成")
            return True
            
        except Exception as e:
            logger.error(f"模型准备失败: {e}")
            return False