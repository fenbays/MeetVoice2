import os
import tempfile
import numpy as np
from typing import Optional
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks
import librosa
import soundfile as sf
from core.utils.model_manager import ModelManager

class DenoisingService:
    """
    基于ModelScope FRCRN模型的音频降噪服务
    """
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
        self.temp_files = []
        self._pipeline = None
        self._init_pipeline()
    
    def _init_pipeline(self):
        """初始化降噪管道"""
        try:
            print("🔄 初始化降噪模型...")
            
            # 获取模型配置
            model_config = self.model_manager.model_config.get_model_config("frcrn-ans")
            model_id = self.model_manager.model_config.get_model_id("frcrn-ans")
            model_path = self.model_manager.model_config.get_model_path("frcrn-ans")
            model_revision = self.model_manager.model_config.get_model_version("frcrn-ans")
            
            if not model_id:
                print("❌ 降噪模型配置未找到")
                return
            
            # 尝试使用本地模型，如果不存在则使用在线模型
            model_source = model_path if os.path.exists(model_path) else model_id
            
            print(f"📦 使用降噪模型: {model_source}")
            
            # 创建降噪管道
            self._pipeline = pipeline(
                Tasks.acoustic_noise_suppression,
                model=model_source,
                model_revision = model_revision
            )
            
            print("✅ 降噪模型初始化完成")
            
        except Exception as e:
            print(f"⚠️ 降噪模型初始化失败: {e}")
            print("🔄 将回退到简单降噪方法")
            self._pipeline = None
    
    def denoise(self, audio_path: str) -> Optional[str]:
        """
        对音频文件进行降噪处理
        
        Args:
            audio_path: 输入音频文件路径
            
        Returns:
            降噪后的音频文件路径，失败返回None
        """
        if not os.path.exists(audio_path):
            print(f"❌ 音频文件不存在: {audio_path}")
            return None
        
        if not self._pipeline:
            print("⚠️ 降噪模型未就绪，跳过降噪处理")
            return audio_path
        
        try:
            print("🔄 开始FRCRN模型降噪...")
            
            # 生成输出路径
            output_path = self._generate_output_path(audio_path)
            
            # 使用ModelScope降噪管道
            result = self._pipeline(
                audio_path,
                output_path=output_path
            )
            
            # 检查结果
            if result and 'output_path' in result and os.path.exists(result['output_path']):
                print(f"✅ FRCRN降噪完成: {result['output_path']}")
                self.temp_files.append(result['output_path'])
                return result['output_path']
            elif os.path.exists(output_path):
                # 有些版本直接输出到指定路径
                print(f"✅ FRCRN降噪完成: {output_path}")
                self.temp_files.append(output_path)
                return output_path
            else:
                print("❌ 降噪输出文件未生成")
                return None
                
        except Exception as e:
            print(f"❌ FRCRN降噪失败: {e}")
            import traceback
            traceback.print_exc()
            
            # 降级到简单处理
            print("🔄 回退到基础降噪方法...")
            return self._fallback_denoise(audio_path)
    
    def _generate_output_path(self, input_path: str) -> str:
        """生成降噪输出文件路径"""
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        temp_dir = tempfile.gettempdir()
        output_path = os.path.join(temp_dir, f"{base_name}_frcrn_denoised.wav")
        return output_path
    
    def _fallback_denoise(self, audio_path: str) -> Optional[str]:
        """
        回退降噪方法：简单的频谱减法
        当FRCRN模型失败时使用
        """
        try:
            import librosa
            import soundfile as sf
            import numpy as np
            
            print("🔄 使用基础频谱减法降噪...")
            
            # 加载音频
            audio, sr = librosa.load(audio_path, sr=16000)  # 统一到16kHz
            
            # 简单的频谱减法降噪
            stft = librosa.stft(audio, n_fft=2048, hop_length=512)
            magnitude = np.abs(stft)
            phase = np.angle(stft)
            
            # 估计噪声谱（前0.5秒）
            noise_frames = int(0.5 * sr / 512)
            noise_spectrum = np.mean(magnitude[:, :noise_frames], axis=1, keepdims=True)
            
            # 频谱减法
            alpha = 1.5  # 保守的过减因子
            beta = 0.1   # 保留比例
            cleaned_magnitude = magnitude - alpha * noise_spectrum
            cleaned_magnitude = np.maximum(cleaned_magnitude, beta * magnitude)
            
            # 重构音频
            cleaned_stft = cleaned_magnitude * np.exp(1j * phase)
            cleaned_audio = librosa.istft(cleaned_stft, hop_length=512)
            
            # 保存结果
            output_path = self._generate_output_path(audio_path).replace("_frcrn_", "_fallback_")
            sf.write(output_path, cleaned_audio, sr)
            
            print(f"✅ 基础降噪完成: {output_path}")
            self.temp_files.append(output_path)
            return output_path
            
        except Exception as e:
            print(f"❌ 基础降噪也失败了: {e}")
            return None
    
    def is_available(self) -> bool:
        """检查降噪服务是否可用"""
        return self._pipeline is not None
    
    def get_model_info(self) -> dict:
        """获取当前使用的模型信息"""
        if self._pipeline:
            return {
                "model": "FRCRN (iic/speech_frcrn_ans_cirm_16k)",
                "type": "ModelScope Pipeline",
                "status": "ready"
            }
        else:
            return {
                "model": "Fallback Spectral Subtraction",
                "type": "Basic Algorithm", 
                "status": "fallback"
            }
    
    def cleanup(self):
        """清理临时文件"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    print(f"🧹 已清理降噪临时文件: {os.path.basename(temp_file)}")
            except Exception as e:
                print(f"⚠️ 清理临时文件失败 {temp_file}: {e}")
        self.temp_files.clear()
        
        # 清理模型管道（如果需要）
        if self._pipeline:
            try:
                # ModelScope管道通常自动管理资源
                pass
            except Exception as e:
                print(f"⚠️ 降噪模型清理失败: {e}")
