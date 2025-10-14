"""
离线音频处理器 - 负责音频合并和说话人分离

设计原则：
1. 无状态 - 每次调用都是独立的
2. 同步方法 - 在线程池中执行，不阻塞主循环
3. 职责单一 - 只负责离线处理，不管实时流
"""
import os
import logging
from typing import Dict, List, Optional, Callable
from core.services.speaker_separation_service import SpeakerSeparationService
from core.utils.media_processor import MediaProcessor

logger = logging.getLogger(__name__)


class OfflineAudioProcessor:
    """离线音频处理器 - 无状态、可复用"""
    
    def __init__(self, speaker_service: SpeakerSeparationService):
        self.speaker_service = speaker_service
    
    def merge_audio_segments(
        self,
        audio_segments: List[bytes],
        output_path: str
    ) -> Optional[str]:
        """
        合并音频段
        
        Args:
            audio_segments: 音频字节列表
            output_path: 输出文件路径
            
        Returns:
            成功返回文件路径，失败返回None
        """
        if not audio_segments:
            logger.warning("No audio segments to merge")
            return None
        
        try:
            total_size = sum(len(seg) for seg in audio_segments)
            logger.info(f"Merging {len(audio_segments)} segments, total: {total_size} bytes")
            
            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 直接拼接字节
            with open(output_path, 'wb') as f:
                for segment in audio_segments:
                    if segment:
                        f.write(segment)
            
            # 验证文件
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                if file_size > 0:
                    logger.info(f"Merged audio created: {output_path} ({file_size} bytes)")
                    return output_path
                else:
                    logger.error("Merged file is empty")
                    return None
            else:
                logger.error("Merged file not created")
                return None
        
        except Exception as e:
            logger.error(f"Failed to merge audio: {e}")
            return None
    
    def process_with_speaker_separation(
        self,
        audio_path: str,
        output_dir: str,
        merge_threshold: int = 10,
        save_audio_segments: bool = True,
        save_merged_audio: bool = True,
        hotwords: Optional[List[str]] = None,
        progress_callback: Optional[Callable] = None
    ) -> Dict:
        """
        执行说话人分离和转录
        
        Args:
            audio_path: 音频文件路径
            output_dir: 输出目录
            merge_threshold: 合并阈值
            save_audio_segments: 是否保存音频片段
            save_merged_audio: 是否保存合并的音频
            hotwords: 热词列表
            progress_callback: 进度回调
            
        Returns:
            处理结果字典
        """
        logger.info(f"Processing audio with speaker separation: {audio_path}")
        
        # 检查文件
        if not os.path.exists(audio_path):
            return {
                'success': False,
                'message': f'Audio file not found: {audio_path}'
            }
        
        try:
            # 准备音频文件
            prepared_audio = self._prepare_audio_file(audio_path)
            if not prepared_audio:
                return {
                    'success': False,
                    'message': 'Failed to prepare audio file'
                }
            
            # 设置热词
            if hotwords:
                self.speaker_service.set_hotwords(hotwords_list=hotwords)
            
            # 执行说话人分离
            result = self.speaker_service.separate_speakers(
                prepared_audio,
                merge_threshold=merge_threshold,
                progress_callback=progress_callback
            )
            
            if result.get('success'):
                # 保存结果
                saved_paths = self.speaker_service.save_separation_results(
                    result,
                    output_dir,
                    save_audio_segments=save_audio_segments,
                    save_merged_audio=save_merged_audio
                )
                result['saved_paths'] = saved_paths
                
                logger.info(f"Speaker separation completed: {len(result.get('speakers', []))} speakers")
            
            return result
        
        except Exception as e:
            logger.error(f"Speaker separation failed: {e}")
            return {
                'success': False,
                'message': str(e)
            }
    
    def _prepare_audio_file(self, media_path: str) -> Optional[str]:
        """
        准备音频文件（如果是视频则提取音频）
        
        Args:
            media_path: 媒体文件路径
            
        Returns:
            音频文件路径，失败返回None
        """
        if not os.path.exists(media_path):
            logger.error(f"File not found: {media_path}")
            return None
        
        # 获取媒体信息
        media_info = MediaProcessor.get_media_info(media_path)
        if not media_info:
            logger.error(f"Failed to get media info: {media_path}")
            return None
        
        logger.info(f"Media type: {media_info['type']}, duration: {media_info['duration']:.2f}s")
        
        if media_info['type'] == 'audio':
            return media_path
        
        elif media_info['type'] == 'video':
            if not media_info['has_audio']:
                logger.error("Video has no audio stream")
                return None
            
            # 提取音频
            audio_path = MediaProcessor.extract_audio_from_video(media_path)
            if audio_path:
                logger.info(f"Audio extracted: {audio_path}")
                return audio_path
            else:
                logger.error("Failed to extract audio")
                return None
        
        else:
            logger.error(f"Unsupported file type: {media_info['type']}")
            return None

