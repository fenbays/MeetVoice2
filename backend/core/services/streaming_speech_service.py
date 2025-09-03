import os
import numpy as np
from typing import Optional, Generator, Callable, Union
import soundfile
import torch
import torchaudio
from core.utils.model_manager import ModelManager

class StreamingSpeechService:
    """流式语音识别服务"""
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
        self.cache = {}
        self.current_model = None
        self.streaming_config = None
    
    def _prepare_streaming_model(self, model_name: str = "paraformer-zh-streaming") -> bool:
        """准备流式模型"""
        try:
            self.current_model = self.model_manager.get_model(model_name)
            if not self.current_model:
                print(f"❌ 无法加载流式模型: {model_name}")
                return False
            
            # 获取流式配置
            self.streaming_config = self.model_manager.model_config.get_streaming_config(model_name)
            if not self.streaming_config:
                print(f"❌ 无法获取流式配置: {model_name}")
                return False
            
            # 重置缓存
            self.cache = {}
            print(f"✓ 流式模型准备完成: {model_name}")
            return True
            
        except Exception as e:
            print(f"❌ 准备流式模型失败: {e}")
            return False
    
    def _calculate_chunk_stride(self, sample_rate: int = 16000) -> int:
        """计算块步长"""
        chunk_stride_ms = self.streaming_config.get("chunk_stride_ms", 600)
        return int(chunk_stride_ms * sample_rate / 1000)  # 转换为采样点数

    def _resample_audio(self, speech: np.ndarray, original_sr: int, target_sr: int = 16000) -> np.ndarray:
        """
        使用FunASR的重采样功能重采样音频
        
        Args:
            speech: 音频数据
            original_sr: 原始采样率
            target_sr: 目标采样率
            
        Returns:
            重采样后的音频数据
        """
        if original_sr == target_sr:
            return speech
            
        try:
            print(f"🔄 使用FunASR重采样功能: {original_sr}Hz -> {target_sr}Hz")
            
            # 转换为torch tensor
            speech_tensor = torch.from_numpy(speech)
            
            # 使用torchaudio的重采样器
            resampler = torchaudio.transforms.Resample(original_sr, target_sr)
            
            # 重采样 (需要添加batch维度)
            if speech_tensor.dim() == 1:
                resampled = resampler(speech_tensor.unsqueeze(0)).squeeze(0)
            else:
                resampled = resampler(speech_tensor)
            
            # 转换回numpy
            resampled_audio = resampled.numpy()
            
            print(f"✅ 重采样完成: 长度 {len(speech)} -> {len(resampled_audio)}")
            return resampled_audio
            
        except Exception as e:
            print(f"❌ 重采样失败: {e}")
            print("⚠️ 将继续使用原始音频，但可能影响识别效果")
            return speech
    
    def stream_recognize_file(self, 
                            audio_file: str, 
                            model_name: str = "paraformer-zh-streaming",
                            callback: Optional[Callable] = None) -> Generator[str, None, None]:
        """
        流式识别音频文件
        
        Args:
            audio_file: 音频文件路径
            model_name: 模型名称
            callback: 结果回调函数
        
        Yields:
            识别结果
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"音频文件不存在: {audio_file}")
        
        # 准备模型
        if not self._prepare_streaming_model(model_name):
            return
        
        try:
            # 读取音频文件
            speech, sample_rate = soundfile.read(audio_file)
            print(f"音频文件: {audio_file}")
            print(f"采样率: {sample_rate}, 时长: {len(speech)/sample_rate:.2f}秒")
            
            # 如果采样率不是16kHz，需要重采样
            if sample_rate != 16000:
                print("⚠️ 音频采样率不是16kHz，正在进行重采样...")
                speech = self._resample_audio(speech, sample_rate, 16000)
                sample_rate = 16000
                print(f"✅ 重采样完成，新采样率: {sample_rate}Hz, 新时长: {len(speech)/sample_rate:.2f}秒")

            
            chunk_stride = self._calculate_chunk_stride(sample_rate)
            total_chunk_num = int((len(speech) - 1) / chunk_stride + 1)
            
            print(f"开始流式识别(file)，共 {total_chunk_num} 个块")
            
            for i in range(total_chunk_num):
                # 提取音频块
                start_idx = i * chunk_stride
                end_idx = min((i + 1) * chunk_stride, len(speech))
                speech_chunk = speech[start_idx:end_idx]
                
                # 是否为最后一块
                is_final = i == total_chunk_num - 1
                
                # 执行识别
                res = self.current_model.generate(
                    input=speech_chunk,
                    cache=self.cache,
                    is_final=is_final,
                    chunk_size=self.streaming_config["chunk_size"],
                    encoder_chunk_look_back=self.streaming_config["encoder_chunk_look_back"],
                    decoder_chunk_look_back=self.streaming_config["decoder_chunk_look_back"]
                )
                
                # 处理结果
                if res and len(res) > 0:
                    text = res[0].get("text", "")
                    if text.strip():
                        result = f"块 {i+1}/{total_chunk_num}: {text}"
                        print(result)
                        
                        # 调用回调函数
                        if callback:
                            callback(i, total_chunk_num, text, is_final)
                        
                        yield text
                
        except Exception as e:
            print(f"❌ 流式识别失败: {e}")
            import traceback
            traceback.print_exc()
    
    def stream_recognize_chunks(self, 
                              audio_chunks: list, 
                              model_name: str = "paraformer-zh-streaming",
                              sample_rate: int = 16000) -> Generator[str, None, None]:
        """
        流式识别音频块序列
        
        Args:
            audio_chunks: 音频块列表
            model_name: 模型名称
            sample_rate: 采样率
        
        Yields:
            识别结果
        """
        # 准备模型
        if not self._prepare_streaming_model(model_name):
            return
        
        # 检查采样率并重采样音频块
        if sample_rate != 16000:
            print(f"⚠️ 音频块采样率不是16kHz ({sample_rate}Hz)，正在重采样...")
            resampled_chunks = []
            for i, chunk in enumerate(audio_chunks):
                resampled_chunk = self._resample_audio(chunk, sample_rate, 16000)
                resampled_chunks.append(resampled_chunk)
            audio_chunks = resampled_chunks
            sample_rate = 16000
            print(f"✅ 所有音频块重采样完成")
        
        total_chunks = len(audio_chunks)
        print(f"开始流式识别(chunks)，共 {total_chunks} 个块")
        
        try:
            for i, chunk in enumerate(audio_chunks):
                is_final = i == total_chunks - 1
                
                # 执行识别
                res = self.current_model.generate(
                    input=chunk,
                    cache=self.cache,
                    is_final=is_final,
                    chunk_size=self.streaming_config["chunk_size"],
                    encoder_chunk_look_back=self.streaming_config["encoder_chunk_look_back"],
                    decoder_chunk_look_back=self.streaming_config["decoder_chunk_look_back"]
                )
                
                # 处理结果
                if res and len(res) > 0:
                    text = res[0].get("text", "")
                    if text.strip():
                        result = f"块 {i+1}/{total_chunks}: {text}"
                        print(result)
                        yield text
                        
        except Exception as e:
            print(f"❌ 流式识别失败: {e}")
            import traceback
            traceback.print_exc()
    
    def reset_cache(self):
        """重置缓存"""
        self.cache = {}
        print("✓ 缓存已重置")