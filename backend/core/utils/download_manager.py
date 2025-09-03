import os
from typing import List, Tuple, Dict, Optional
from modelscope import snapshot_download
from conf.model import ModelConfig

class DownloadManager:
    def __init__(self, model_config: ModelConfig):
        self.model_config = model_config
        os.makedirs(self.model_config.model_lib, exist_ok=True)

    def download_model(self, model_name: str, force_download: bool = False) -> Tuple[bool, str]:
        """
        下载单个模型
        
        Args:
            model_name: 模型名称
            force_download: 是否强制重新下载
            
        Returns:
            (成功状态, 消息)
        """
        try:
            model_id = self.model_config.get_model_id(model_name)
            if not model_id:
                return False, f"未找到模型配置: {model_name}"

            local_path = self.model_config.get_model_path(model_name)
            description = self.model_config.get_model_description(model_name)
            version = self.model_config.get_model_version(model_name)
            
            # 检查本地是否已存在
            if not force_download and os.path.exists(local_path):
                return True, f"模型 {model_name} 已存在: {local_path}"

            print(f"🔄 开始下载模型: {model_name}")
            print(f"   📝 描述: {description}")
            print(f"   📦 版本: {version}")
            print(f"   🆔 ID: {model_id}")
            
            # 下载模型，指定版本
            download_kwargs = {
                "model_id": model_id,
                "cache_dir": self.model_config.model_lib
            }
            
            # 如果有版本信息，添加版本参数
            if version and version != "latest":
                download_kwargs["revision"] = version
            
            model_dir = snapshot_download(**download_kwargs)
            
            print(f"✅ 模型 {model_name} 下载完成: {model_dir}")
            return True, model_dir
            
        except Exception as e:
            error_msg = f"下载模型 {model_name} 失败: {str(e)}"
            print(f"❌ {error_msg}")
            return False, error_msg

    def download_required_models(self) -> List[Tuple[str, bool, str]]:
        """下载所有必需的模型"""
        print("\n🔄 开始下载必需模型...")
        results = []
        required_models = self.model_config.get_required_models()
        
        for model_name in required_models:
            print(f"\n处理必需模型: {model_name}")
            success, message = self.download_model(model_name)
            results.append((model_name, success, message))
        
        return results

    def download_speaker_separation_models(self, force_download: bool = False) -> List[Tuple[str, bool, str]]:
        """
        下载说话人分离相关的所有模型
        
        Args:
            force_download: 是否强制重新下载
            
        Returns:
            下载结果列表
        """
        print("\n🔄 开始下载说话人分离模型...")
        results = []
        speaker_models = self.model_config.get_speaker_separation_models()
        
        for model_name in speaker_models:
            print(f"\n处理说话人分离模型: {model_name}")
            success, message = self.download_model(model_name, force_download)
            results.append((model_name, success, message))
        
        return results

    def download_models_by_category(self, category: str, force_download: bool = False) -> List[Tuple[str, bool, str]]:
        """
        按分类下载模型
        
        Args:
            category: 模型分类
            force_download: 是否强制重新下载
            
        Returns:
            下载结果列表
        """
        print(f"\n🔄 开始下载 {category} 分类的模型...")
        results = []
        category_models = self.model_config.get_models_by_category(category)
        
        for model_name in category_models:
            print(f"\n处理 {category} 模型: {model_name}")
            success, message = self.download_model(model_name, force_download)
            results.append((model_name, success, message))
        
        return results

    def download_all_models(self, force_download: bool = False) -> List[Tuple[str, bool, str]]:
        """
        下载所有模型
        
        Args:
            force_download: 是否强制重新下载
            
        Returns:
            下载结果列表
        """
        print("\n🔄 开始下载所有模型...")
        results = []
        
        for model_name in self.model_config.model_configs:
            print(f"\n处理模型: {model_name}")
            success, message = self.download_model(model_name, force_download)
            results.append((model_name, success, message))
        
        return results

    def check_model_status(self) -> Dict[str, dict]:
        """
        检查所有模型的状态
        
        Returns:
            模型状态字典
        """
        status = {}
        
        for model_name, config in self.model_config.model_configs.items():
            local_path = config["local_path"]
            exists = os.path.exists(local_path)
            
            status[model_name] = {
                "name": model_name,
                "description": config.get("description", ""),
                "category": config.get("category", "unknown"),
                "required": config.get("required", False),
                "exists": exists,
                "local_path": local_path,
                "model_id": config.get("model_id", ""),
                "version": config.get("version", "latest")
            }
        
        return status

    def print_model_status(self):
        """打印所有模型的状态"""
        status = self.check_model_status()
        
        print("\n" + "="*80)
        print("📦 模型状态检查")
        print("="*80)
        
        # 按分类组织
        categories = {}
        for model_name, info in status.items():
            category = info["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append((model_name, info))
        
        for category, models in categories.items():
            print(f"\n🏷️  {category.replace('_', ' ').title()}:")
            print("-" * 50)
            
            for model_name, info in models:
                # 状态标记
                status_mark = "✅ 已安装" if info["exists"] else "❌ 未安装"
                required_mark = "🔴 必需" if info["required"] else "🟡 可选"
                
                print(f"   {status_mark} {required_mark} {model_name}")
                print(f"      📝 {info['description']}")
                if info["exists"]:
                    print(f"      📁 路径: {info['local_path']}")
                else:
                    print(f"      🆔 ID: {info['model_id']}")
                print()
        
        # 统计信息
        total_models = len(status)
        installed_count = sum(1 for info in status.values() if info["exists"])
        required_count = sum(1 for info in status.values() if info["required"])
        required_installed = sum(1 for info in status.values() if info["required"] and info["exists"])
        
        print("📊 统计信息:")
        print(f"   总模型数: {total_models}")
        print(f"   已安装: {installed_count}/{total_models}")
        print(f"   必需模型: {required_installed}/{required_count}")
        
        if required_installed < required_count:
            print("⚠️  警告: 部分必需模型未安装，可能影响核心功能")
        
        print("="*80)

    def get_missing_required_models(self) -> List[str]:
        """获取缺失的必需模型列表"""
        missing = []
        status = self.check_model_status()
        
        for model_name, info in status.items():
            if info["required"] and not info["exists"]:
                missing.append(model_name)
        
        return missing

    def get_missing_speaker_models(self) -> List[str]:
        """获取缺失的说话人分离模型列表"""
        missing = []
        status = self.check_model_status()
        speaker_models = self.model_config.get_speaker_separation_models()
        
        for model_name in speaker_models:
            if model_name in status and not status[model_name]["exists"]:
                missing.append(model_name)
        
        return missing

    def download_missing_models(self, include_optional: bool = False) -> List[Tuple[str, bool, str]]:
        """
        下载缺失的模型
        
        Args:
            include_optional: 是否包含可选模型
            
        Returns:
            下载结果列表
        """
        results = []
        status = self.check_model_status()
        
        for model_name, info in status.items():
            if not info["exists"]:
                # 必需模型总是下载
                if info["required"]:
                    print(f"\n下载缺失的必需模型: {model_name}")
                    success, message = self.download_model(model_name)
                    results.append((model_name, success, message))
                # 可选模型根据参数决定
                elif include_optional:
                    print(f"\n下载缺失的可选模型: {model_name}")
                    success, message = self.download_model(model_name)
                    results.append((model_name, success, message))
        
        return results
    
    def download_denoising_models(self) -> List[tuple]:
        """
        下载降噪相关模型
        
        Returns:
            下载结果列表: [(model_name, success, message), ...]
        """
        print("\n🔄 检查降噪模型...")
        
        denoising_models = ["frcrn-ans"]
        results = []
        
        for model_name in denoising_models:
            print(f"\n📦 检查模型: {model_name}")
            
            try:
                success, message = self.download_model(model_name)
                results.append((model_name, success, message))
                
                if success:
                    print(f"✅ {model_name}: {message}")
                else:
                    print(f"❌ {model_name}: {message}")
                    
            except Exception as e:
                error_msg = f"下载过程异常: {e}"
                print(f"❌ {model_name}: {error_msg}")
                results.append((model_name, False, error_msg))
        
        return results
    
    def download_all_models_with_denoising(self) -> Dict[str, List[tuple]]:
        """
        下载所有模型（包括降噪模型）
        
        Returns:
            分类下载结果
        """
        return {
            "speech_recognition": self.download_speech_models(),
            "speaker_separation": self.download_speaker_separation_models(),
            "denoising": self.download_denoising_models()
        }