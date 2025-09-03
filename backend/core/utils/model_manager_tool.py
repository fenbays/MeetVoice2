#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型管理工具
用于下载、检查和管理DotVoice的所有模型
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conf.model import ModelConfig
from core.utils.download_manager import DownloadManager

def main_menu():
    """显示主菜单"""
    print("\n" + "="*60)
    print("📦 DotVoice 模型管理工具")
    print("="*60)
    print("1. 📊 查看模型状态")
    print("2. 📝 查看模型配置")
    print("3. 🔄 下载必需模型")
    print("4. 👥 下载说话人分离模型")
    print("5. 📁 按分类下载模型")
    print("6. 🔄 下载所有模型")
    print("7. 🔍 下载缺失模型")
    print("8. 🧹 检查并修复")
    print("9. 🚪 退出")
    print("="*60)

def show_models_by_category(config: ModelConfig):
    """按分类显示模型"""
    categories = ["speech_recognition", "speaker_separation", "voice_activity_detection", "text_processing", "speaker_recognition"]
    
    print("\n选择要查看的分类:")
    for i, category in enumerate(categories, 1):
        models = config.get_models_by_category(category)
        print(f"   {i}. {category.replace('_', ' ').title()} ({len(models)} 个模型)")
    
    try:
        choice = int(input(f"\n请选择 (1-{len(categories)}): ")) - 1
        if 0 <= choice < len(categories):
            category = categories[choice]
            models = config.get_models_by_category(category)
            
            print(f"\n🏷️  {category.replace('_', ' ').title()} 模型:")
            print("-" * 50)
            
            for model_name, model_config in models.items():
                required_mark = "🔴 必需" if model_config.get("required", False) else "🟡 可选"
                print(f"   {required_mark} {model_name}")
                print(f"      📝 {model_config.get('description', '无描述')}")
                print(f"      🆔 {model_config.get('model_id', '')}")
                print()
        else:
            print("❌ 无效的选择")
    except (ValueError, IndexError):
        print("❌ 输入无效")

def download_by_category(download_manager: DownloadManager):
    """按分类下载模型"""
    categories = ["speech_recognition", "speaker_separation", "voice_activity_detection", "text_processing", "speaker_recognition"]
    
    print("\n选择要下载的分类:")
    for i, category in enumerate(categories, 1):
        models = download_manager.model_config.get_models_by_category(category)
        print(f"   {i}. {category.replace('_', ' ').title()} ({len(models)} 个模型)")
    
    try:
        choice = int(input(f"\n请选择 (1-{len(categories)}): ")) - 1
        if 0 <= choice < len(categories):
            category = categories[choice]
            
            force = input("是否强制重新下载? (y/N): ").strip().lower() == 'y'
            
            print(f"\n🔄 开始下载 {category} 分类的模型...")
            results = download_manager.download_models_by_category(category, force_download=force)
            
            success_count = sum(1 for _, success, _ in results if success)
            print(f"\n📊 下载完成: {success_count}/{len(results)} 个模型成功")
            
            # 显示详细结果
            for name, success, message in results:
                status = "✅" if success else "❌"
                print(f"   {status} {name}: {message}")
        else:
            print("❌ 无效的选择")
    except (ValueError, IndexError):
        print("❌ 输入无效")

def main():
    """主函数"""
    print("📦 DotVoice 模型管理工具")
    print("正在初始化...")
    
    try:
        config = ModelConfig()
        download_manager = DownloadManager(config)
        print("✅ 初始化完成")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return
    
    while True:
        try:
            main_menu()
            choice = input("\n请选择功能 (1-9): ").strip()
            
            if choice == '1':
                # 查看模型状态
                download_manager.print_model_status()
                
            elif choice == '2':
                # 查看模型配置
                config.print_model_summary()
                
            elif choice == '3':
                # 下载必需模型
                print("\n🔄 开始下载必需模型...")
                results = download_manager.download_required_models()
                success_count = sum(1 for _, success, _ in results if success)
                print(f"\n📊 下载完成: {success_count}/{len(results)} 个模型成功")
                
            elif choice == '4':
                # 下载说话人分离模型
                force = input("是否强制重新下载? (y/N): ").strip().lower() == 'y'
                results = download_manager.download_speaker_separation_models(force_download=force)
                success_count = sum(1 for _, success, _ in results if success)
                print(f"\n📊 下载完成: {success_count}/{len(results)} 个模型成功")
                
            elif choice == '5':
                # 按分类下载模型
                download_by_category(download_manager)
                
            elif choice == '6':
                # 下载所有模型
                force = input("是否强制重新下载? (y/N): ").strip().lower() == 'y'
                print("\n🔄 开始下载所有模型...")
                results = download_manager.download_all_models(force_download=force)
                success_count = sum(1 for _, success, _ in results if success)
                print(f"\n📊 下载完成: {success_count}/{len(results)} 个模型成功")
                
            elif choice == '7':
                # 下载缺失模型
                include_optional = input("是否包含可选模型? (y/N): ").strip().lower() == 'y'
                results = download_manager.download_missing_models(include_optional=include_optional)
                
                if results:
                    success_count = sum(1 for _, success, _ in results if success)
                    print(f"\n📊 下载完成: {success_count}/{len(results)} 个模型成功")
                else:
                    print("\n✅ 没有缺失的模型需要下载")
                
            elif choice == '8':
                # 检查并修复
                print("\n🔍 检查模型状态...")
                missing_required = download_manager.get_missing_required_models()
                missing_speaker = download_manager.get_missing_speaker_models()
                
                if missing_required:
                    print(f"⚠️ 缺失必需模型: {missing_required}")
                    if input("是否立即下载? (Y/n): ").strip().lower() != 'n':
                        results = download_manager.download_required_models()
                        success_count = sum(1 for _, success, _ in results if success)
                        print(f"📊 必需模型下载: {success_count}/{len(results)} 个成功")
                
                if missing_speaker:
                    print(f"⚠️ 缺失说话人分离模型: {missing_speaker}")
                    if input("是否下载说话人分离模型? (y/N): ").strip().lower() == 'y':
                        results = download_manager.download_speaker_separation_models()
                        success_count = sum(1 for _, success, _ in results if success)
                        print(f"📊 说话人分离模型下载: {success_count}/{len(results)} 个成功")
                
                if not missing_required and not missing_speaker:
                    print("✅ 所有模型状态正常")
                
            elif choice == '9':
                print("👋 感谢使用模型管理工具!")
                break
                
            else:
                print("❌ 无效的选择，请重新输入")
                
        except KeyboardInterrupt:
            print("\n👋 用户中断，退出程序")
            break
        except Exception as e:
            print(f"❌ 操作失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main() 