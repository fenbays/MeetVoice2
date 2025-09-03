from typing import Optional, Dict
from funasr import AutoModel
from conf.model import ModelConfig

class ModelManager:
    def __init__(self, model_config: ModelConfig):
        self.model_config = model_config
        self.loaded_models: Dict[str, AutoModel] = {}

    def load_model(self, model_name: str) -> Optional[AutoModel]:
        """加载指定的模型"""
        if model_name in self.loaded_models:
            return self.loaded_models[model_name]

        model_path = self.model_config.get_model_path(model_name)
        model_config = self.model_config.get_model_config(model_name)
        
        if not model_path or not model_config:
            print(f"未找到模型配置: {model_name}")
            return None

        try:
            print(f"正在加载模型: {model_name}")
            model = AutoModel(
                model=model_path,
                **model_config  # 展开模型配置
            )
            self.loaded_models[model_name] = model
            print(f"✓ 模型 {model_name} 加载成功")
            return model
        except Exception as e:
            print(f"加载模型失败 {model_name}: {str(e)}")
            return None

    def get_model(self, model_name: str) -> Optional[AutoModel]:
        """获取已加载的模型或加载新模型"""
        return self.load_model(model_name)

    def unload_model(self, model_name: str) -> bool:
        """卸载模型以释放内存"""
        if model_name in self.loaded_models:
            del self.loaded_models[model_name]
            print(f"✓ 模型 {model_name} 已卸载")
            return True
        return False

    def list_loaded_models(self) -> list:
        """列出已加载的模型"""
        return list(self.loaded_models.keys())