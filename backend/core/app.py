import sys
import os
import time
from typing import List, Tuple, Dict, Optional, Callable
from config.model_config import ModelConfig
from utils.download_manager import DownloadManager
from utils.model_manager import ModelManager
from utils.media_processor import MediaProcessor
from services.audio_processor import AudioProcessor
from services.speech_service import SpeechRecognitionService
from services.streaming_speech_service import StreamingSpeechService

def initialize_models() -> bool:
    """初始化并下载所需模型"""
    model_config = ModelConfig()
    download_manager = DownloadManager(model_config)
    
    print("正在检查并下载必需模型...")
    results = download_manager.download_required_models()
    
    all_success = True
    for model_name, success, message in results:
        if not success:
            print(f"错误: {message}")
            all_success = False
    
    return all_success

def find_project_example_audio() -> List[str]:
    """查找项目根目录下 example 文件夹中的音频和视频文件"""
    example_files = []
    
    # 获取项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example_dir = os.path.join(project_root, "example")
    
    print(f"查找媒体文件目录: {example_dir}")
    
    if not os.path.exists(example_dir):
        print(f"❌ example 目录不存在: {example_dir}")
        print("💡 请在项目根目录创建 example 文件夹并放入音频/视频文件")
        return example_files
    
    # 支持的媒体格式
    media_extensions = (
        # 音频格式
        '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
        # 视频格式
        '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v'
    )
    
    try:
        # 递归查找所有媒体文件
        for root, dirs, files in os.walk(example_dir):
            for file in files:
                if file.lower().endswith(media_extensions):
                    full_path = os.path.join(root, file)
                    example_files.append(full_path)
                    relative_path = os.path.relpath(full_path, example_dir)
                    
                    # 显示文件类型
                    if file.lower().endswith(('.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v')):
                        print(f"✓ 找到视频: {relative_path}")
                    else:
                        print(f"✓ 找到音频: {relative_path}")
        
        # 按文件名排序
        example_files.sort()
        
    except Exception as e:
        print(f"❌ 查找媒体文件时出错: {e}")
    
    return example_files

def compare_recognition_modes(processor: AudioProcessor, audio_file: str, language: str = "auto") -> Tuple[str, str, dict]:
    """比较离线和流式识别模式"""
    file_name = os.path.basename(audio_file)
    results = {
        'file_name': file_name,
        'offline_result': None,
        'streaming_result': None,
        'offline_time': 0,
        'streaming_time': 0,
        'offline_error': None,
        'streaming_error': None
    }
    
    print(f"\n{'='*60}")
    print(f"🎵 测试文件: {file_name}")
    print(f"📂 路径: {audio_file}")
    print(f"🌐 语言: {language}")
    print(f"{'='*60}")
    
    print(f"\n🔄 正在进行流式识别...")
    try:
        start_time = time.time()
        streaming_result = processor.process_single_audio(
            audio_file, 
            language=language, 
            streaming=True
        )
        streaming_time = time.time() - start_time
        
        results['streaming_result'] = streaming_result
        results['streaming_time'] = streaming_time
        
        print(f"✅ 流式识别完成 (耗时: {streaming_time:.2f}秒)")
        print(f"📝 结果: {streaming_result}")
        
    except Exception as e:
        results['streaming_error'] = str(e)
        print(f"❌ 流式识别失败: {e}")

    print(f"\n🔄 正在进行离线识别...")
    try:
        start_time = time.time()
        offline_result = processor.process_single_audio(
            audio_file, 
            language=language, 
            streaming=False
        )
        offline_time = time.time() - start_time
        
        results['offline_result'] = offline_result
        results['offline_time'] = offline_time
        
        print(f"✅ 离线识别完成 (耗时: {offline_time:.2f}秒)")
        print(f"📝 结果: {offline_result}")
        
    except Exception as e:
        results['offline_error'] = str(e)
        print(f"❌ 离线识别失败: {e}")

    # 结果对比
    print(f"\n📊 结果对比:")
    print(f"┌─────────────┬─────────────────────────────────────────────────────────┐")
    print(f"│ 模式        │ 结果                                                    │")
    print(f"├─────────────┼─────────────────────────────────────────────────────────┤")
    
    offline_display = results['offline_result'] if results['offline_result'] else f"错误: {results['offline_error']}"
    streaming_display = results['streaming_result'] if results['streaming_result'] else f"错误: {results['streaming_error']}"
    
    # 截断过长的文本
    offline_display = offline_display[:50] + "..." if len(offline_display) > 50 else offline_display
    streaming_display = streaming_display[:50] + "..." if len(streaming_display) > 50 else streaming_display
    
    print(f"│ 离线模式    │ {offline_display:<55} │")
    print(f"│ 流式模式    │ {streaming_display:<55} │")
    print(f"└─────────────┴─────────────────────────────────────────────────────────┘")
    
    if results['offline_time'] > 0 and results['streaming_time'] > 0:
        print(f"⏱️  性能对比:")
        print(f"   离线模式: {results['offline_time']:.2f}秒")
        print(f"   流式模式: {results['streaming_time']:.2f}秒")
        
        if results['offline_time'] < results['streaming_time']:
            print(f"   🏆 离线模式更快 ({results['streaming_time']/results['offline_time']:.1f}x)")
        else:
            print(f"   🏆 流式模式更快 ({results['offline_time']/results['streaming_time']:.1f}x)")
    
    return results['offline_result'], results['streaming_result'], results

def auto_test_all_examples(processor: AudioProcessor, example_files: List[str]):
    """自动测试所有示例音频文件"""
    if not example_files:
        print("❌ 没有找到示例音频文件")
        return
    
    print(f"\n🚀 开始自动测试 {len(example_files)} 个音频文件")
    print(f"{'='*80}")
    
    all_results = []
    
    for i, audio_file in enumerate(example_files, 1):
        file_name = os.path.basename(audio_file)
        
        # 根据文件名智能判断语言
        language = "auto"
        if "en" in file_name.lower() or "english" in file_name.lower():
            language = "en"
        elif "zh" in file_name.lower() or "cn" in file_name.lower() or "chinese" in file_name.lower():
            language = "zh"
        elif "ja" in file_name.lower() or "japanese" in file_name.lower():
            language = "ja"
        elif "ko" in file_name.lower() or "korean" in file_name.lower():
            language = "ko"
        elif "yue" in file_name.lower() or "cantonese" in file_name.lower():
            language = "yue"
        
        print(f"\n进度: [{i}/{len(example_files)}]")
        
        try:
            offline_result, streaming_result, detailed_results = compare_recognition_modes(
                processor, audio_file, language
            )
            all_results.append(detailed_results)
            
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            continue
        
        # 每个文件测试后暂停一下，让用户看清结果
        if i < len(example_files):
            input(f"\n按 Enter 继续测试下一个文件... ({i}/{len(example_files)})")
    
    # 生成总结报告
    generate_summary_report(all_results)

def generate_summary_report(all_results: List[dict]):
    """生成测试总结报告"""
    if not all_results:
        return
    
    print(f"\n{'='*80}")
    print(f"📋 测试总结报告")
    print(f"{'='*80}")
    
    total_files = len(all_results)
    offline_success = sum(1 for r in all_results if r['offline_result'] is not None)
    streaming_success = sum(1 for r in all_results if r['streaming_result'] is not None)
    
    print(f"📊 统计信息:")
    print(f"   总文件数: {total_files}")
    print(f"   离线成功: {offline_success}/{total_files} ({offline_success/total_files*100:.1f}%)")
    print(f"   流式成功: {streaming_success}/{total_files} ({streaming_success/total_files*100:.1f}%)")
    
    # 性能统计
    offline_times = [r['offline_time'] for r in all_results if r['offline_time'] > 0]
    streaming_times = [r['streaming_time'] for r in all_results if r['streaming_time'] > 0]
    
    if offline_times and streaming_times:
        avg_offline = sum(offline_times) / len(offline_times)
        avg_streaming = sum(streaming_times) / len(streaming_times)
        
        print(f"\n⏱️  平均耗时:")
        print(f"   离线模式: {avg_offline:.2f}秒")
        print(f"   流式模式: {avg_streaming:.2f}秒")
        
        if avg_offline < avg_streaming:
            print(f"   🏆 离线模式平均更快 ({avg_streaming/avg_offline:.1f}x)")
        else:
            print(f"   🏆 流式模式平均更快 ({avg_offline/avg_streaming:.1f}x)")
    
    # 详细结果表格
    print(f"\n📋 详细结果:")
    print(f"┌─────────────────────────┬──────────┬──────────┬─────────┬─────────┐")
    print(f"│ 文件名                  │ 离线状态 │ 流式状态 │ 离线耗时│ 流式耗时│")
    print(f"├─────────────────────────┼──────────┼──────────┼─────────┼─────────┤")
    
    for result in all_results:
        name = result['file_name'][:23]  # 截断文件名
        offline_status = "✅" if result['offline_result'] else "❌"
        streaming_status = "✅" if result['streaming_result'] else "❌"
        offline_time = f"{result['offline_time']:.1f}s" if result['offline_time'] > 0 else "N/A"
        streaming_time = f"{result['streaming_time']:.1f}s" if result['streaming_time'] > 0 else "N/A"
        
        print(f"│ {name:<23} │ {offline_status:<8} │ {streaming_status:<8} │ {offline_time:<7} │ {streaming_time:<7} │")
    
    print(f"└─────────────────────────┴──────────┴──────────┴─────────┴─────────┘")

def main():
    print("🎵 Dot Voice 语音识别自动测试系统")
    print("📁 自动查找项目根目录下 example 文件夹中的音频/视频文件")
    print("🎬 支持视频格式: MP4, AVI, MOV, WMV, FLV, MKV, WEBM, M4V")
    print("🎵 支持音频格式: MP3, WAV, M4A, FLAC, AAC, OGG, WMA")
    
    # 初始化模型
    print("\n🔧 初始化模型...")
    if not initialize_models():
        print("❌ 模型初始化失败，程序退出")
        sys.exit(1)
    
    # 创建音频处理器
    print("🔧 创建音频处理器...")
    processor = AudioProcessor()
    
    try:
        # 查找项目根目录下的示例音频文件
        print("\n🔍 查找示例音频文件...")
        example_files = find_project_example_audio()
        
        if not example_files:
            print("\n💡 请在项目根目录创建 example 文件夹并放入音频/视频文件")
            print("   支持的格式: .mp3, .wav, .m4a, .flac, .aac, .ogg, .wma, .mp4, .avi, .mov, .wmv, .flv, .mkv, .webm, .m4v")
            return
        
        print(f"\n✅ 找到 {len(example_files)} 个媒体文件")
        
        # 询问是否开始自动测试
        response = input(f"\n🚀 是否开始自动测试所有音频文件? (y/n，默认y): ").strip().lower()
        if response != 'n':
            auto_test_all_examples(processor, example_files)
        else:
            print("✋ 用户取消测试")
        
    except Exception as e:
        print(f"❌ 运行时错误: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        processor.cleanup()
        print("\n👋 程序退出")

if __name__ == "__main__":
    main()