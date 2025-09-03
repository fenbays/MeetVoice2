#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
说话人分离功能演示脚本
"""

import os
from typing import Optional, Dict, List
from services.audio_processor import AudioProcessor
from utils.download_manager import DownloadManager
from config.model_config import ModelConfig  # 添加这行导入

def progress_callback(message: str, progress: int):
    """进度回调函数"""
    if progress >= 0:
        print(f"[{progress:3d}%] {message}")
    else:
        print(f"[ERROR] {message}")

def _select_audio_file(audio_files: List[str]) -> Optional[str]:
    """
    选择音频文件的辅助函数
    
    Args:
        audio_files: 音频文件列表
        
    Returns:
        选中的音频文件路径，取消或错误返回None
    """
    if len(audio_files) == 1:
        # 只有一个文件，直接返回
        return audio_files[0]
    
    print("\n请选择要处理的文件:")
    for i, file in enumerate(audio_files, 1):
        file_name = os.path.basename(file)
        file_size = os.path.getsize(file) / (1024 * 1024)  # MB
        print(f"   {i}. {file_name} ({file_size:.1f}MB)")
    
    try:
        while True:
            file_choice = input(f"\n请输入文件编号 (1-{len(audio_files)}): ").strip()
            
            if not file_choice:
                print("❌ 请输入有效的文件编号")
                continue
                
            try:
                choice_idx = int(file_choice) - 1
                if 0 <= choice_idx < len(audio_files):
                    selected_file = audio_files[choice_idx]
                    print(f"✅ 已选择: {os.path.basename(selected_file)}")
                    return selected_file
                else:
                    print(f"❌ 编号超出范围，请输入 1-{len(audio_files)} 之间的数字")
            except ValueError:
                print("❌ 请输入有效的数字")
                
    except KeyboardInterrupt:
        print("\n👋 用户取消选择")
        return None
    except Exception as e:
        print(f"❌ 选择文件时出错: {e}")
        return None

def _print_analysis_result(result: Dict):
    """
    打印完整分析结果的辅助函数
    
    Args:
        result: 分析结果字典
    """
    if result.get('success', False):
        print(f"\n✅ 完整分析完成!")
        
        # 显示降噪状态和模型信息
        if result.get('denoising_enabled', False):
            denoising_model = result.get('denoising_model', {})
            model_name = denoising_model.get('model', 'Unknown')
            model_type = denoising_model.get('type', 'Unknown')
            model_status = denoising_model.get('status', 'unknown')
            
            if model_status == 'ready':
                print(f"🔧 降噪模型: {model_name} ({model_type})")
            elif model_status == 'fallback':
                print(f"🔧 降噪方法: {model_name} (FRCRN模型不可用时的回退方案)")
            else:
                print(f"🔧 降噪: 已启用但状态未知")
        else:
            print("🔧 降噪: 未启用")
        
        # 显示语音识别结果
        speech_result = result.get('speech_recognition')
        if speech_result:
            # 智能截取预览文本
            preview_length = 100
            if len(speech_result) > preview_length:
                text_preview = speech_result[:preview_length] + "..."
                full_length = len(speech_result)
                print(f"🎤 语音识别结果 ({full_length}字符): {text_preview}")
            else:
                print(f"🎤 语音识别结果: {speech_result}")
        else:
            print("🎤 语音识别: 无结果")
        
        # 显示说话人分离结果
        speaker_result = result.get('speaker_separation')
        if speaker_result and speaker_result.get('success', False):
            speakers = speaker_result.get('speakers', [])
            speaker_count = len(speakers)
            print(f"👥 说话人分离: 检测到 {speaker_count} 个说话人")
            
            # 显示每个说话人的简要信息
            for i, speaker in enumerate(speakers[:3], 1):  # 最多显示前3个
                duration = speaker.get('total_duration', 0)
                segment_count = len(speaker.get('segments', []))
                print(f"   说话人{i}: {duration:.1f}秒, {segment_count}个片段")
            
            if len(speakers) > 3:
                print(f"   ...还有{len(speakers) - 3}个说话人")
            
            # 显示保存路径
            saved_paths = speaker_result.get('saved_paths', {})
            base_dir = saved_paths.get('base_dir')
            if base_dir:
                print(f"📁 结果保存在: {base_dir}")
                
                # 显示主要输出文件
                if 'summary_file' in saved_paths:
                    print(f"   📄 分析报告: {os.path.basename(saved_paths['summary_file'])}")
                if 'merged_audio_files' in saved_paths:
                    merged_files = saved_paths['merged_audio_files']
                    if merged_files:
                        print(f"   🎵 合并音频: {len(merged_files)}个文件")
        else:
            speaker_error = speaker_result.get('message', '未知错误') if speaker_result else '无结果'
            print(f"👥 说话人分离: 失败 ({speaker_error})")
        
        # 显示处理信息
        message = result.get('message', '')
        if message and message != '音频分析完成':
            print(f"💬 处理信息: {message}")
            
    else:
        # 分析失败
        error_message = result.get('message', '未知错误')
        print(f"\n❌ 分析失败: {error_message}")
        
        # 如果有部分结果，也显示出来
        if result.get('speech_recognition'):
            print(f"🎤 语音识别(部分): {result['speech_recognition'][:50]}...")
        
        if result.get('speaker_separation'):
            speaker_msg = result['speaker_separation'].get('message', '')
            if speaker_msg:
                print(f"👥 说话人分离错误: {speaker_msg}")


def demo_speaker_separation():
    """演示说话人分离功能"""
    print("=== DotVoice 说话人分离功能演示 ===\n")
    
    # 1. 检查并下载模型
    print("🔄 检查说话人分离模型...")
    model_config = ModelConfig()  # 创建 ModelConfig 实例
    download_manager = DownloadManager(model_config)  # 传入 ModelConfig 实例
    
    # 下载模型并检查结果
    results = download_manager.download_speaker_separation_models()
    if not all(success for _, success, _ in results):
        print("❌ 部分模型下载失败:")
        for model_name, success, message in results:
            if not success:
                print(f"   - {model_name}: {message}")
        return
    
    print("✅ 所有必需模型下载完成")
    
    # 2. 初始化处理器
    print("\n🔄 初始化音频处理器...")
    try:
        processor = AudioProcessor()
        print("✅ 音频处理器初始化完成")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return
    
    # 3. 查找示例音频文件
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example_dir = os.path.join(project_root, "example")
    
    if not os.path.exists(example_dir):
        print(f"❌ 示例目录不存在: {example_dir}")
        print("💡 请在项目根目录创建 example 文件夹并放入音频文件")
        return
    
    # 查找音频文件
    audio_files = []
    for file in os.listdir(example_dir):
        if file.lower().endswith(('.mp3', '.wav', '.m4a', '.flac', '.aac', '.mp4', '.avi', '.mov')):
            audio_files.append(os.path.join(example_dir, file))
    
    if not audio_files:
        print(f"❌ 在 {example_dir} 中没有找到音频文件")
        return
    
    print(f"\n📁 找到 {len(audio_files)} 个媒体文件:")
    for i, file in enumerate(audio_files, 1):
        print(f"   {i}. {os.path.basename(file)}")
    
    # 4. 选择处理方式
    print("\n请选择处理方式:")
    print("1. 单文件说话人分离")
    print("2. 批量说话人分离")
    print("3. 完整音频分析（语音识别 + 说话人分离）")
    print("4. 完整音频分析（语音识别 + 说话人分离） + FRCRN模型降噪（推荐用于嘈杂环境）")
    print("5. 退出")
    
    try:
        choice = input("\n请输入选择 (1-4): ").strip()
    except KeyboardInterrupt:
        print("\n👋 用户取消操作")
        return
    
    # 设置输出目录
    output_dir = os.path.join(project_root, "output", "speaker_separation")
    os.makedirs(output_dir, exist_ok=True)
    
    # 设置热词（可选）
    hotwords = ["AI", "zabbix", "snmp"]  # 示例热词
    
    try:
        if choice == "1":
            # 单文件处理
            if len(audio_files) == 1:
                audio_file = audio_files[0]
            else:
                print("\n请选择要处理的文件:")
                for i, file in enumerate(audio_files, 1):
                    print(f"   {i}. {os.path.basename(file)}")
                
                file_choice = int(input("请输入文件编号: ")) - 1
                if 0 <= file_choice < len(audio_files):
                    audio_file = audio_files[file_choice]
                else:
                    print("❌ 无效的文件编号")
                    return
            
            print(f"\n🔄 处理文件: {os.path.basename(audio_file)}")
            result = processor.separate_speakers(
                audio_file,
                output_dir,
                merge_threshold=10,
                save_audio_segments=True,
                save_merged_audio=True,
                hotwords=hotwords,
                progress_callback=progress_callback
            )
            
            if result['success']:
                print(f"\n✅ 处理完成!")
                print(f"📊 检测到 {len(result['speakers'])} 个说话人")
                print(f"📁 结果保存在: {result['saved_paths']['base_dir']}")
            else:
                print(f"\n❌ 处理失败: {result['message']}")
        
        elif choice == "2":
            # 批量处理
            print(f"\n🔄 批量处理 {len(audio_files)} 个文件...")
            results = processor.batch_separate_speakers(
                audio_files,
                output_dir,
                merge_threshold=10,
                save_audio_segments=True,
                save_merged_audio=True,
                hotwords=hotwords,
                progress_callback=progress_callback
            )
            
            success_count = sum(1 for r in results if r.get('success', False))
            print(f"\n✅ 批量处理完成: {success_count}/{len(results)} 个文件成功")
            print(f"📁 结果保存在: {output_dir}")
        
        elif choice == "3":
            # 完整分析
            if len(audio_files) == 1:
                audio_file = audio_files[0]
            else:
                print("\n请选择要分析的文件:")
                for i, file in enumerate(audio_files, 1):
                    print(f"   {i}. {os.path.basename(file)}")
                
                file_choice = int(input("请输入文件编号: ")) - 1
                if 0 <= file_choice < len(audio_files):
                    audio_file = audio_files[file_choice]
                else:
                    print("❌ 无效的文件编号")
                    return
            
            print(f"\n🔄 完整分析文件: {os.path.basename(audio_file)}")
            result = processor.analyze_audio_with_all_features(
                audio_file,
                output_dir,
                language="auto",
                merge_threshold=10,
                hotwords=hotwords
            )
            
            if result['success']:
                print(f"\n✅ 完整分析完成!")
                if result['speech_recognition']:
                    print(f"🎤 语音识别结果: {result['speech_recognition'][:100]}...")
                if result['speaker_separation'] and result['speaker_separation']['success']:
                    print(f"👥 说话人分离: 检测到 {len(result['speaker_separation']['speakers'])} 个说话人")
                    print(f"📁 结果保存在: {result['speaker_separation']['saved_paths']['base_dir']}")
            else:
                print(f"\n❌ 分析失败: {result['message']}")
        
        elif choice == "4":
            # 完整分析（启用FRCRN降噪）
            audio_file = _select_audio_file(audio_files)
            if not audio_file:
                return
            
            print(f"\n🔄 完整分析文件（启用FRCRN降噪）: {os.path.basename(audio_file)}")
            print("💡 FRCRN模型能有效去除背景噪声，提升语音识别准确率")
            print("⏱️  降噪处理可能需要额外时间，请耐心等待...")
            
            result = processor.analyze_audio_with_all_features(
                audio_file,
                output_dir,
                language="auto",
                merge_threshold=10,
                hotwords=hotwords,
                enable_denoising=True  # 启用FRCRN降噪
            )
            
            _print_analysis_result(result)

        elif choice == "5":
            print("👋 退出程序")
            return
        
        else:
            print("❌ 无效的选择")
            return
    
    except (ValueError, IndexError):
        print("❌ 输入无效")
        return
    except Exception as e:
        print(f"❌ 处理过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理资源
        processor.cleanup()

if __name__ == "__main__":
    demo_speaker_separation()