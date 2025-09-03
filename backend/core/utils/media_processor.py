import os
import subprocess
import tempfile
from typing import Dict, Optional, Tuple
import soundfile as sf

class MediaProcessor:
    """媒体文件处理工具"""
    
    @staticmethod
    def is_video_file(file_path: str) -> bool:
        """判断是否为视频文件"""
        video_extensions = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v'}
        return os.path.splitext(file_path.lower())[1] in video_extensions
    
    @staticmethod
    def is_audio_file(file_path: str) -> bool:
        """判断是否为音频文件"""
        audio_extensions = {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma'}
        return os.path.splitext(file_path.lower())[1] in audio_extensions
    
    @staticmethod
    def extract_audio_from_video(video_path: str, 
                                output_path: Optional[str] = None,
                                sample_rate: int = 16000) -> Optional[str]:
        """
        从视频文件中提取音频
        
        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径，如果为None则创建临时文件
            sample_rate: 采样率，默认16000Hz
            
        Returns:
            提取的音频文件路径，失败返回None
        """
        if not os.path.exists(video_path):
            print(f"❌ 视频文件不存在: {video_path}")
            return None
        
        try:
            # 创建输出文件路径
            if output_path is None:
                # 创建临时文件
                temp_fd, output_path = tempfile.mkstemp(suffix='.wav', prefix='extracted_audio_')
                os.close(temp_fd)  # 关闭文件描述符，只保留路径
            
            print(f"🎬 正在从视频文件提取音频...")
            print(f"   输入: {video_path}")
            print(f"   输出: {output_path}")
            
            # 使用 ffmpeg 提取音频
            cmd = [
                'ffmpeg',
                '-i', video_path,           # 输入视频文件
                '-vn',                      # 不处理视频流
                '-acodec', 'pcm_s16le',     # 音频编码器
                '-ar', str(sample_rate),    # 采样率
                '-ac', '1',                 # 单声道
                '-y',                       # 覆盖输出文件
                output_path                 # 输出文件
            ]
            
            # 执行命令
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            # 验证输出文件
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                # 获取音频信息
                try:
                    data, sr = sf.read(output_path)
                    duration = len(data) / sr
                    print(f"✅ 音频提取成功")
                    print(f"   时长: {duration:.2f}秒")
                    print(f"   采样率: {sr}Hz")
                    print(f"   声道: {1 if data.ndim == 1 else data.shape[1]}")
                    return output_path
                except Exception as e:
                    print(f"⚠️ 无法读取提取的音频文件: {e}")
                    return output_path  # 仍然返回路径，让后续处理尝试
            else:
                print("❌ 音频提取失败：输出文件不存在或为空")
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"❌ ffmpeg 处理失败:")
            print(f"   返回码: {e.returncode}")
            print(f"   错误信息: {e.stderr}")
            return None
        except FileNotFoundError:
            print("❌ 未找到 ffmpeg，请确保已安装 ffmpeg")
            print("   Ubuntu/Debian: sudo apt install ffmpeg")
            print("   macOS: brew install ffmpeg")
            print("   Windows: 从 https://ffmpeg.org/ 下载")
            return None
        except Exception as e:
            print(f"❌ 音频提取失败: {e}")
            return None
    
    @staticmethod
    def get_media_info(file_path: str) -> Optional[Dict]:
        """
        获取媒体文件信息
        
        Args:
            file_path: 媒体文件路径
            
        Returns:
            包含媒体信息的字典
        """
        if not os.path.exists(file_path):
            return None
            
        try:
            if MediaProcessor.is_audio_file(file_path):
                # 音频文件
                data, sample_rate = sf.read(file_path)
                return {
                    'type': 'audio',
                    'duration': len(data) / sample_rate,
                    'sample_rate': sample_rate,
                    'channels': 1 if data.ndim == 1 else data.shape[1],
                    'file_size': os.path.getsize(file_path)
                }
            elif MediaProcessor.is_video_file(file_path):
                # 视频文件 - 使用ffprobe获取信息
                cmd = [
                    'ffprobe', 
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    file_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                import json
                info = json.loads(result.stdout)
                
                # 提取基本信息
                format_info = info.get('format', {})
                duration = float(format_info.get('duration', 0))
                file_size = int(format_info.get('size', 0))
                
                # 查找音频流
                audio_streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
                has_audio = len(audio_streams) > 0
                
                return {
                    'type': 'video',
                    'duration': duration,
                    'file_size': file_size,
                    'has_audio': has_audio,
                    'audio_streams': len(audio_streams)
                }
            else:
                return None
                
        except Exception as e:
            print(f"⚠️ 获取媒体信息失败: {e}")
            return None
    
    @staticmethod
    def cleanup_temp_file(file_path: str):
        """清理临时文件"""
        if file_path and os.path.exists(file_path) and 'tmp' in file_path:
            try:
                os.remove(file_path)
                print(f"🗑️ 已清理临时文件: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"⚠️ 清理临时文件失败: {e}")
                
