import os
from typing import Dict, Optional
from django.conf import settings

class ModelConfig:
    def __init__(self):
        self.project_root = settings.BASE_DIR
        self.model_lib = os.path.join(self.project_root, "model_lib")
        
        self.model_configs: Dict[str, dict] = {
            "frcrn-ans": {
                "model_id": "iic/speech_frcrn_ans_cirm_16k",
                "local_path": os.path.join(self.model_lib, "iic", "speech_frcrn_ans_cirm_16k"),
                "required": False,
                "description": "FRCRN声学降噪模型，基于CIRM掩码的16kHz音频降噪，有效去除背景噪声",
                "category": "audio_enhancement",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu",
                    "output_dir": "output"
                },
                "type": "enhancement",
                "version": "v1.0.0"
            },
            "sense_voice": {
                "model_id": "iic/SenseVoiceSmall",
                "local_path": os.path.join(self.model_lib, "iic", "SenseVoiceSmall"),
                "required": True,
                "description": "高精度多语言语音识别模型，支持中英文混合识别，具备情感识别和事件检测能力",
                "category": "speech_recognition",
                "config": {
                    "vad_model": "fsmn-vad",
                    "vad_kwargs": {"max_single_segment_time": 30000},
                    "device": "cuda:0" if self._is_cuda_available() else "cpu"
                },
                "type": "offline"
            },
            "paraformer-zh-streaming": {
                "model_id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
                "local_path": os.path.join(self.model_lib, "iic", "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"),
                "required": True,
                "description": "中文流式语音识别模型，支持实时语音转文字，低延迟高准确率",
                "category": "speech_recognition",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu",                    
                },
                "type": "streaming",
                "version": "v2.0.4",
                "streaming_config": {
                    "chunk_size": [0, 10, 5],  # [0, 10, 5] 600ms, [0, 8, 4] 480ms
                    "encoder_chunk_look_back": 4,
                    "decoder_chunk_look_back": 1,
                    "chunk_stride_ms": 600  # 600ms stride
                }
            },
            
            # === 说话人分离相关模型 ===
            "paraformer-zh-large": {
                "model_id": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                "local_path": os.path.join(self.model_lib, "iic", "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
                "required": False,
                "description": "大型中文语音识别模型，用于说话人分离场景下的高精度语音识别",
                "category": "speaker_separation",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu",
                    "batch_size_s": 300
                },
                "type": "offline",
                "version": "v2.0.4"
            },
            "fsmn-vad": {
                "model_id": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                "local_path": os.path.join(self.model_lib, "iic", "speech_fsmn_vad_zh-cn-16k-common-pytorch"),
                "required": False,
                "description": "基于FSMN的语音活动检测模型，用于检测语音的起止时间点",
                "category": "voice_activity_detection",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu"
                },
                "type": "utility",
                "version": "v2.0.4"
            },
            "punc-transformer": {
                "model_id": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
                "local_path": os.path.join(self.model_lib, "iic", "punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
                "required": False,
                "description": "基于Transformer的中文标点符号预测模型，为识别结果添加标点符号",
                "category": "text_processing",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu"
                },
                "type": "utility",
                "version": "v2.0.4"
            },
            "campplus-speaker": {
                "model_id": "iic/speech_campplus_sv_zh-cn_16k-common",
                "local_path": os.path.join(self.model_lib, "iic", "speech_campplus_sv_zh-cn_16k-common"),
                "required": False,
                "description": "基于CAM++的说话人识别模型，用于区分不同说话人的语音特征",
                "category": "speaker_recognition",
                "config": {
                    "device": "cuda:0" if self._is_cuda_available() else "cpu"
                },
                "type": "utility",
                "version": "v2.0.2"
            }
        }
    
    def _is_cuda_available(self) -> bool:
        """检查CUDA是否可用"""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    def get_model_path(self, model_name: str) -> Optional[str]:
        """获取模型本地路径"""
        if model_name in self.model_configs:
            return self.model_configs[model_name]["local_path"]
        return None

    def get_model_id(self, model_name: str) -> Optional[str]:
        """获取模型在线ID"""
        if model_name in self.model_configs:
            return self.model_configs[model_name]["model_id"]
        return None

    def get_model_config(self, model_name: str) -> Optional[dict]:
        """获取模型配置"""
        if model_name in self.model_configs:
            return self.model_configs[model_name]["config"]
        return None

    def get_streaming_config(self, model_name: str) -> Optional[dict]:
        """获取流式配置"""
        if model_name in self.model_configs:
            return self.model_configs[model_name].get("streaming_config")
        return None
    
    def get_model_description(self, model_name: str) -> Optional[str]:
        """获取模型描述"""
        if model_name in self.model_configs:
            return self.model_configs[model_name].get("description", "")
        return None
    
    def get_model_category(self, model_name: str) -> Optional[str]:
        """获取模型分类"""
        if model_name in self.model_configs:
            return self.model_configs[model_name].get("category", "unknown")
        return None
    
    def get_model_version(self, model_name: str) -> Optional[str]:
        """获取模型版本"""
        if model_name in self.model_configs:
            return self.model_configs[model_name].get("version", "latest")
        return None
    
    def is_streaming_model(self, model_name: str) -> bool:
        """检查是否为流式模型"""
        return self.model_configs.get(model_name, {}).get("type") == "streaming"

    def is_model_required(self, model_name: str) -> bool:
        """检查模型是否必需"""
        return self.model_configs.get(model_name, {}).get("required", False)
    
    def get_models_by_category(self, category: str) -> Dict[str, dict]:
        """根据分类获取模型列表"""
        models = {}
        for model_name, config in self.model_configs.items():
            if config.get("category") == category:
                models[model_name] = config
        return models
    
    def get_speaker_separation_models(self) -> Dict[str, dict]:
        """获取说话人分离相关的所有模型"""
        speaker_models = {}
        categories = ["speaker_separation", "voice_activity_detection", "text_processing", "speaker_recognition"]
        
        for category in categories:
            models = self.get_models_by_category(category)
            speaker_models.update(models)
        
        return speaker_models
    
    def get_required_models(self) -> Dict[str, dict]:
        """获取所有必需的模型"""
        required = {}
        for model_name, config in self.model_configs.items():
            if config.get("required", False):
                required[model_name] = config
        return required
    
    def get_optional_models(self) -> Dict[str, dict]:
        """获取所有可选的模型"""
        optional = {}
        for model_name, config in self.model_configs.items():
            if not config.get("required", False):
                optional[model_name] = config
        return optional
    
    def list_all_models(self) -> Dict[str, dict]:
        """列出所有模型的详细信息"""
        model_info = {}
        for model_name, config in self.model_configs.items():
            model_info[model_name] = {
                "name": model_name,
                "description": config.get("description", ""),
                "category": config.get("category", "unknown"),
                "type": config.get("type", "unknown"),
                "required": config.get("required", False),
                "version": config.get("version", "latest"),
                "model_id": config.get("model_id", ""),
                "local_path": config.get("local_path", "")
            }
        return model_info
    
    def print_model_summary(self):
        """打印模型配置摘要"""
        print("\n" + "="*80)
        print("📦 DotVoice 模型配置摘要")
        print("="*80)
        
        # 按分类组织模型
        categories = {}
        for model_name, config in self.model_configs.items():
            category = config.get("category", "unknown")
            if category not in categories:
                categories[category] = []
            categories[category].append((model_name, config))
        
        # 打印每个分类的模型
        for category, models in categories.items():
            print(f"\n🏷️  {category.replace('_', ' ').title()}:")
            print("-" * 50)
            
            for model_name, config in models:
                required_mark = "🔴 必需" if config.get("required", False) else "🟡 可选"
                print(f"   {required_mark} {model_name}")
                print(f"      📝 {config.get('description', '无描述')}")
                print(f"      🏷️  类型: {config.get('type', 'unknown')}")
                print(f"      📦 版本: {config.get('version', 'latest')}")
                print(f"      🆔 ID: {config.get('model_id', '')}")
                print()
        
        # 统计信息
        total_models = len(self.model_configs)
        required_count = len(self.get_required_models())
        optional_count = len(self.get_optional_models())
        speaker_count = len(self.get_speaker_separation_models())
        
        print("📊 统计信息:")
        print(f"   总模型数: {total_models}")
        print(f"   必需模型: {required_count}")
        print(f"   可选模型: {optional_count}")
        print(f"   说话人分离模型: {speaker_count}")
        print("="*80)