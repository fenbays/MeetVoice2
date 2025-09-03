import os
from typing import Optional, Union, Dict, Any
from funasr.utils.postprocess_utils import rich_transcription_postprocess
from core.utils.model_manager import ModelManager

class SpeechRecognitionService:
    """语音识别服务"""
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
        self.default_params = {
            "cache": {},
            "language": "auto",
            "use_itn": True,
            "batch_size_s": 60,
            "merge_vad": True,
            "merge_length_s": 15,
        }
    
    def recognize(self, 
                  audio_input: Union[str, bytes],
                  model_name: str = "sense_voice",
                  language: str = "auto",
                  **kwargs) -> Optional[str]:
        """
        语音识别主方法
        
        Args:
            audio_input: 音频文件路径或音频数据
            model_name: 使用的模型名称
            language: 语言代码 ("auto", "zh", "en", "yue", "ja", "ko", "nospeech")
            **kwargs: 其他参数
        
        Returns:
            识别的文本结果
        """
        # 获取模型
        model = self.model_manager.get_model(model_name)
        if not model:
            print(f"❌ 无法获取模型: {model_name}")
            return None
        
        try:
            # 合并参数
            params = {**self.default_params, **kwargs}
            params.update({
                "input": audio_input,
                "language": language
            })
            
            print(f"开始语音识别: {audio_input}")
            print(f"使用模型: {model_name}, 语言: {language}")
            
            # 执行识别
            res = model.generate(**params)
            
            if res and len(res) > 0 and "text" in res[0]:
                # 后处理
                text = rich_transcription_postprocess(res[0]["text"])
                print(f"✓ 识别完成")
                return text
            else:
                print("❌ 识别结果为空")
                return None
                
        except Exception as e:
            print(f"❌ 语音识别失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    def recognize_file(self, 
                       file_path: str, 
                       **kwargs) -> Optional[str]:
        """识别音频文件"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"音频文件不存在: {file_path}")
        
        return self.recognize(file_path, **kwargs)
    
    def batch_recognize(self, 
                        file_paths: list, 
                        **kwargs) -> Dict[str, Optional[str]]:
        """批量识别音频文件"""
        results = {}
        for file_path in file_paths:
            try:
                result = self.recognize_file(file_path, **kwargs)
                results[file_path] = result
            except Exception as e:
                print(f"处理文件 {file_path} 时出错: {e}")
                results[file_path] = None
        
        return results
    
    def set_default_params(self, **params):
        """设置默认参数"""
        self.default_params.update(params)