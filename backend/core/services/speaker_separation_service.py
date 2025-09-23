import os
import ffmpeg
import time
import torch
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Callable
from pydub import AudioSegment
from funasr import AutoModel
from core.utils.model_manager import ModelManager
from core.utils.media_processor import MediaProcessor
from core.utils.download_manager import DownloadManager

class SpeakerSeparationService:
    """说话人分离服务"""
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
        self.model = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.hotwords = ""
        self._init_models()
        
    def _init_models(self):
        """初始化说话人分离所需的模型"""
        try:
            print("🔄 正在初始化说话人分离模型...")
            
            # 检查说话人分离模型是否可用
            download_manager = DownloadManager(self.model_manager.model_config)
            
            # 检查缺失的说话人分离模型
            missing_models = download_manager.get_missing_speaker_models()
            if missing_models:
                print(f"⚠️ 缺失说话人分离模型: {missing_models}")
                print("🔄 正在下载缺失的模型...")
                
                results = download_manager.download_speaker_separation_models()
                failed_models = [name for name, success, _ in results if not success]
                
                if failed_models:
                    raise Exception(f"说话人分离模型下载失败: {failed_models}")
                
                print("✅ 说话人分离模型下载完成")
            
            # 获取模型路径
            config = self.model_manager.model_config
            asr_model_path = config.get_model_path("paraformer-zh-large")
            vad_model_path = config.get_model_path("fsmn-vad")
            punc_model_path = config.get_model_path("punc-transformer")
            spk_model_path = config.get_model_path("campplus-speaker")
            
            # 检查模型路径
            required_paths = {
                "ASR模型": asr_model_path,
                "VAD模型": vad_model_path,
                "标点模型": punc_model_path,
                "说话人模型": spk_model_path
            }
            
            for name, path in required_paths.items():
                if not path or not os.path.exists(path):
                    raise Exception(f"{name}路径无效: {path}")
            
            # 获取模型版本
            asr_version = config.get_model_version("paraformer-zh-large")
            vad_version = config.get_model_version("fsmn-vad")
            punc_version = config.get_model_version("punc-transformer")
            spk_version = config.get_model_version("campplus-speaker")
            
            # 创建AutoModel实例
            self.model = AutoModel(
                model=asr_model_path,
                model_revision=asr_version,
                vad_model=vad_model_path,
                vad_model_revision=vad_version,
                punc_model=punc_model_path,
                punc_model_revision=punc_version,
                spk_model=spk_model_path,
                spk_model_revision=spk_version,
                ngpu=1 if torch.cuda.is_available() else 0,
                ncpu=os.cpu_count(),
                device=self.device,
                disable_pbar=True,
                disable_log=True,
                disable_update=True
            )
            
            print(f"✅ 说话人分离模型初始化完成 (设备: {self.device})")
            print(f"   📦 ASR模型: {os.path.basename(asr_model_path)} ({asr_version})")
            print(f"   📦 VAD模型: {os.path.basename(vad_model_path)} ({vad_version})")
            print(f"   📦 标点模型: {os.path.basename(punc_model_path)} ({punc_version})")
            print(f"   📦 说话人模型: {os.path.basename(spk_model_path)} ({spk_version})")
            
        except Exception as e:
            print(f"❌ 说话人分离模型初始化失败: {e}")
            raise e
    
    def set_hotwords(self, hotwords_file: Optional[str] = None, hotwords_list: Optional[List[str]] = None):
        """设置热词"""
        if hotwords_file and os.path.exists(hotwords_file):
            with open(hotwords_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                lines = [line.strip() for line in lines if line.strip()]
            self.hotwords = " ".join(lines)
            print(f"📝 从文件加载热词: {self.hotwords}")
        elif hotwords_list:
            self.hotwords = " ".join(hotwords_list)
            print(f"📝 设置热词: {self.hotwords}")
        else:
            self.hotwords = ""
    
    def _to_date(self, milliseconds: int) -> str:
        """将时间戳转换为SRT格式的时间"""
        time_obj = timedelta(milliseconds=milliseconds)
        hours = time_obj.seconds // 3600
        minutes = (time_obj.seconds // 60) % 60
        seconds = time_obj.seconds % 60
        microseconds = time_obj.microseconds // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{microseconds:03d}"
    
    def _preprocess_audio(self, audio_file: str) -> bytes:
        """预处理音频文件，转换为模型所需格式"""
        try:
            print(f"🔄 预处理音频文件: {audio_file}")
            
            # 使用ffmpeg预处理音频
            audio_bytes, _ = (
                ffmpeg.input(audio_file, threads=0)
                .output("-", format="wav", acodec="pcm_s16le", ac=1, ar=16000)
                .run(cmd=["ffmpeg", "-nostdin"], capture_stdout=True, capture_stderr=True)
            )
            
            print(f"✅ 音频预处理完成")
            return audio_bytes
            
        except Exception as e:
            print(f"❌ 音频预处理失败: {e}")
            raise e
    
    def separate_speakers(self, 
                         audio_file: str,
                         merge_threshold: int = 10,
                         progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        执行说话人分离
        
        Args:
            audio_file: 音频文件路径
            merge_threshold: 合并相邻相同说话人的字数阈值
            progress_callback: 进度回调函数
            
        Returns:
            包含分离结果的字典
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"音频文件不存在: {audio_file}")
        
        try:
            if progress_callback:
                progress_callback("开始说话人分离...", 0)
            
            # 预处理音频
            audio_bytes = self._preprocess_audio(audio_file)
            
            if progress_callback:
                progress_callback("正在执行语音识别和说话人分离...", 30)
            
            # 执行语音识别和说话人分离
            start_time = time.time()
            res = self.model.generate(
                input=audio_bytes, 
                batch_size_s=300, 
                is_final=True, 
                sentence_timestamp=True, 
                hotword=self.hotwords
            )
            end_time = time.time()
            
            if progress_callback:
                progress_callback("处理识别结果...", 70)
            
            rec_result = res[0]
            asr_result_text = rec_result['text']
            
            if not asr_result_text:
                print("⚠️ 没有检测到语音内容")
                return {
                    'success': False,
                    'message': '没有检测到语音内容',
                    'full_text': '',
                    'speakers': {},
                    'sentences': [],
                    'processing_time': end_time - start_time
                }
            
            # 处理句子信息，合并相邻相同说话人的短句
            sentences = []
            for sentence in rec_result["sentence_info"]:
                start = self._to_date(sentence["start"])
                end = self._to_date(sentence["end"])
                
                # 如果是相同说话人且当前句子长度小于阈值，则合并
                if (sentences and 
                    sentence["spk"] == sentences[-1]["spk"] and 
                    len(sentences[-1]["text"]) < merge_threshold):
                    sentences[-1]["text"] += sentence["text"]
                    sentences[-1]["end"] = end
                else:
                    sentences.append({
                        "text": sentence["text"],
                        "start": start,
                        "end": end,
                        "spk": sentence["spk"]
                    })
            
            # 按说话人分组
            speakers = {}
            for sentence in sentences:
                spk_id = sentence["spk"]
                if spk_id not in speakers:
                    speakers[spk_id] = {
                        'segments': [],
                        'total_text': '',
                        'total_duration': 0
                    }
                
                speakers[spk_id]['segments'].append({
                    'text': sentence['text'],
                    'start': sentence['start'],
                    'end': sentence['end']
                })
                speakers[spk_id]['total_text'] += sentence['text']
            
            if progress_callback:
                progress_callback("分离完成", 100)
            
            print(f"✅ 说话人分离完成，耗时: {end_time - start_time:.2f}秒")
            print(f"📊 检测到 {len(speakers)} 个说话人")
            for spk_id, info in speakers.items():
                print(f"   说话人 {spk_id}: {len(info['segments'])} 个片段")
            
            return {
                'success': True,
                'message': f'成功分离出 {len(speakers)} 个说话人',
                'full_text': asr_result_text,
                'speakers': speakers,
                'sentences': sentences,
                'processing_time': end_time - start_time,
                'audio_file': audio_file
            }
            
        except Exception as e:
            error_msg = f"说话人分离失败: {e}"
            print(f"❌ {error_msg}")
            if progress_callback:
                progress_callback(f"错误: {error_msg}", -1)
            return {
                'success': False,
                'message': error_msg,
                'full_text': '',
                'speakers': {},
                'sentences': [],
                'processing_time': 0
            }
    
    def save_separation_results(self, 
                              separation_result: Dict[str, Any], 
                              output_dir: str,
                              save_audio_segments: bool = True,
                              save_merged_audio: bool = True) -> Dict[str, str]:
        """
        保存说话人分离结果
        
        Args:
            separation_result: 分离结果
            output_dir: 输出目录
            save_audio_segments: 是否保存音频片段
            save_merged_audio: 是否保存合并的音频
            
        Returns:
            保存路径信息
        """
        if not separation_result['success']:
            raise ValueError("分离结果无效")
        
        try:
            audio_file = separation_result['audio_file']
            audio_name = os.path.splitext(os.path.basename(audio_file))[0]
            speakers = separation_result['speakers']
            sentences = separation_result['sentences']
            
            # 创建输出目录
            date = datetime.now().strftime("%Y-%m-%d")
            final_output_dir = os.path.join(output_dir, date, audio_name)
            os.makedirs(final_output_dir, exist_ok=True)
            
            saved_paths = {
                'base_dir': final_output_dir,
                'text_files': {},
                'audio_segments': {},
                'merged_audio': {}
            }
            
            # 保存每个说话人的文本
            for spk_id, info in speakers.items():
                # 创建说话人目录
                spk_dir = os.path.join(final_output_dir, f"speaker_{spk_id}")
                os.makedirs(spk_dir, exist_ok=True)
                
                # 保存文本文件
                text_file = os.path.join(spk_dir, f"speaker_{spk_id}.txt")
                with open(text_file, 'w', encoding='utf-8') as f:
                    for segment in info['segments']:
                        f.write(f"{segment['start']} --> {segment['end']}\n")
                        f.write(f"{segment['text']}\n\n")
                
                saved_paths['text_files'][spk_id] = text_file
                
                # 保存音频片段（如果需要）
                if save_audio_segments:
                    segment_files = []
                    for i, segment in enumerate(info['segments']):
                        segment_file = os.path.join(spk_dir, f"segment_{i:03d}.wav")
                        self._extract_audio_segment(
                            audio_file, 
                            segment['start'], 
                            segment['end'], 
                            segment_file
                        )
                        segment_files.append(segment_file)
                    
                    saved_paths['audio_segments'][spk_id] = segment_files
                    
                    # 合并说话人的所有音频片段（如果需要）
                    if save_merged_audio and segment_files:
                        merged_file = os.path.join(spk_dir, f"speaker_{spk_id}_merged.mp3")
                        self._merge_audio_segments(segment_files, merged_file)
                        saved_paths['merged_audio'][spk_id] = merged_file
            
            # 保存完整的分离报告
            report_file = os.path.join(final_output_dir, "separation_report.txt")
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(f"说话人分离报告\n")
                f.write(f"=" * 50 + "\n")
                f.write(f"音频文件: {audio_file}\n")
                f.write(f"处理时间: {separation_result['processing_time']:.2f}秒\n")
                f.write(f"检测到说话人数量: {len(speakers)}\n\n")
                
                for spk_id, info in speakers.items():
                    f.write(f"说话人 {spk_id}:\n")
                    f.write(f"  片段数量: {len(info['segments'])}\n")
                    f.write(f"  总文本: {info['total_text'][:100]}...\n\n")
            
            saved_paths['report'] = report_file
            
            print(f"✅ 分离结果已保存到: {final_output_dir}")
            return saved_paths
            
        except Exception as e:
            print(f"❌ 保存分离结果失败: {e}")
            raise e
    
    def _extract_audio_segment(self, audio_file: str, start_time: str, end_time: str, output_file: str):
        """提取音频片段"""
        try:
            (
                ffmpeg.input(audio_file, ss=start_time, to=end_time)
                .output(output_file)
                .run(cmd=["ffmpeg", "-nostdin"], overwrite_output=True, capture_stdout=True, capture_stderr=True)
            )
        except Exception as e:
            print(f"⚠️ 提取音频片段失败: {e}")
    
    def _merge_audio_segments(self, segment_files: List[str], output_file: str):
        """合并音频片段"""
        try:
            if not segment_files:
                return
            
            # 使用pydub合并音频
            combined = AudioSegment.from_file(segment_files[0])
            for segment_file in segment_files[1:]:
                if os.path.exists(segment_file):
                    segment = AudioSegment.from_file(segment_file)
                    combined += segment
            
            combined.export(output_file, format="mp3")
            print(f"✅ 音频片段已合并到: {output_file}")
            
        except Exception as e:
            print(f"⚠️ 合并音频片段失败: {e}")
    
    def batch_separate_speakers(self, 
                              audio_files: List[str], 
                              output_dir: str,
                              merge_threshold: int = 10,
                              progress_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """
        批量处理多个音频文件的说话人分离
        
        Args:
            audio_files: 音频文件列表
            output_dir: 输出目录
            merge_threshold: 合并阈值
            progress_callback: 进度回调函数
            
        Returns:
            处理结果列表
        """
        results = []
        total_files = len(audio_files)
        
        for i, audio_file in enumerate(audio_files):
            try:
                if progress_callback:
                    progress_callback(f"处理文件 {i+1}/{total_files}: {os.path.basename(audio_file)}", 
                                    int((i / total_files) * 100))
                
                print(f"\n🔄 处理文件 {i+1}/{total_files}: {audio_file}")
                
                # 执行说话人分离
                result = self.separate_speakers(audio_file, merge_threshold)
                
                if result['success']:
                    # 保存结果
                    saved_paths = self.save_separation_results(result, output_dir)
                    result['saved_paths'] = saved_paths
                
                results.append(result)
                
            except Exception as e:
                error_result = {
                    'success': False,
                    'message': f'处理文件失败: {e}',
                    'audio_file': audio_file
                }
                results.append(error_result)
                print(f"❌ 处理文件失败: {audio_file}, 错误: {e}")
        
        if progress_callback:
            progress_callback("批量处理完成", 100)
        
        return results
