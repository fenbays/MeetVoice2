import asyncio
import logging
from enum import Enum
from typing import Optional, Callable
import contextlib

logger = logging.getLogger(__name__)

ERROR_INSTALL_INSTRUCTIONS = """
FFmpeg is not installed or not found in your system's PATH.
Please install FFmpeg to enable audio processing.

Installation instructions:

# Ubuntu/Debian:
sudo apt update && sudo apt install ffmpeg

# macOS (using Homebrew):
brew install ffmpeg

# Windows:
# 1. Download the latest static build from https://ffmpeg.org/download.html
# 2. Extract the archive (e.g., to C:\\FFmpeg).
# 3. Add the 'bin' directory (e.g., C:\\FFmpeg\\bin) to your system's PATH environment variable.

After installation, please restart the application.
"""

class FFmpegState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RESTARTING = "restarting"
    FAILED = "failed"

class FFmpegAudioManager:
    """
    简洁的FFmpeg音频管理器 - 只负责数据传输
    """
    
    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
                
        self.process: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.on_error_callback: Optional[Callable[[str], None]] = None

        self.state = FFmpegState.STOPPED
        self._state_lock = asyncio.Lock()

    async def start(self) -> bool:
        """启动FFmpeg进程 - 使用WhisperLiveKit的方式"""
        async with self._state_lock:
            if self.state != FFmpegState.STOPPED:
                logger.warning(f"FFmpeg already running in state: {self.state}")
                return False
            self.state = FFmpegState.STARTING

        try:
            # 完全按照WhisperLiveKit的方式配置FFmpeg
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",      # 改回error级别
                "-i", "pipe:0",            # 关键：不指定输入格式，让FFmpeg自动检测
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "pipe:1"
            ]

            logger.info(f"启动FFmpeg命令: {' '.join(cmd)}")

            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 等待进程稳定启动
            await asyncio.sleep(0.1)  # WhisperLiveKit没有这个检查，但保留以防万一
            
            if self.process.returncode is not None:
                logger.error(f"FFmpeg进程启动后立即退出，返回码: {self.process.returncode}")
                stderr_output = await self.process.stderr.read()
                logger.error(f"FFmpeg错误输出: {stderr_output.decode(errors='ignore')}")
                async with self._state_lock:
                    self.state = FFmpegState.FAILED
                return False

            self._stderr_task = asyncio.create_task(self._drain_stderr())

            async with self._state_lock:
                self.state = FFmpegState.RUNNING

            logger.info("FFmpeg started successfully.")
            return True

        except FileNotFoundError:
            logger.error(ERROR_INSTALL_INSTRUCTIONS)
            async with self._state_lock:
                self.state = FFmpegState.FAILED
            if self.on_error_callback:
                await self.on_error_callback("ffmpeg_not_found")
            return False

        except Exception as e:
            logger.error(f"Error starting FFmpeg: {e}")
            async with self._state_lock:
                self.state = FFmpegState.FAILED
            if self.on_error_callback:
                await self.on_error_callback("start_failed")
            return False

    async def stop(self):
        """停止FFmpeg进程"""
        async with self._state_lock:
            if self.state == FFmpegState.STOPPED:
                return
            self.state = FFmpegState.STOPPED

        if self.process:
            try:
                if self.process.stdin and not self.process.stdin.is_closing():
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
                
                # 给进程一些时间正常退出
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("FFmpeg进程未能在5秒内退出，强制终止")
                    self.process.terminate()
                    await self.process.wait()
                    
            except Exception as e:
                logger.error(f"停止FFmpeg进程时出错: {e}")
            finally:
                self.process = None

        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task

        logger.info("FFmpeg stopped.")

    async def write_data(self, data: bytes) -> bool:
        """写入音频数据到FFmpeg - 简化版本"""
        async with self._state_lock:
            if self.state != FFmpegState.RUNNING:
                logger.warning(f"Cannot write, FFmpeg state: {self.state}")
                return False
            
            if not self.process or self.process.returncode is not None:
                logger.error(f"FFmpeg进程已退出")
                self.state = FFmpegState.FAILED
                return False

        try:
            if self.process.stdin.is_closing():
                logger.error("FFmpeg stdin已关闭")
                return False
                
            # 直接写入，不进行累积处理
            self.process.stdin.write(data)
            await self.process.stdin.drain()
            return True
            
        except BrokenPipeError:
            logger.error("FFmpeg管道已断开")
            async with self._state_lock:
                self.state = FFmpegState.FAILED
            if self.on_error_callback:
                await self.on_error_callback("pipe_broken")
            return False
            
        except Exception as e:
            logger.error(f"Error writing to FFmpeg: {e}")
            async with self._state_lock:
                self.state = FFmpegState.FAILED
            if self.on_error_callback:
                await self.on_error_callback("write_error")
            return False

    async def read_data(self, size: int) -> Optional[bytes]:
        """从FFmpeg读取PCM数据"""
        async with self._state_lock:
            if self.state != FFmpegState.RUNNING:
                logger.warning(f"Cannot read, FFmpeg state: {self.state}")
                return None
            
            # 检查进程是否还活着
            if not self.process or self.process.returncode is not None:
                logger.error(f"FFmpeg进程已退出，返回码: {self.process.returncode if self.process else 'None'}")
                self.state = FFmpegState.FAILED
                return None

        try:
            data = await asyncio.wait_for(
                self.process.stdout.read(size),
                timeout=20.0
            )
            return data
        except asyncio.TimeoutError:
            logger.warning("FFmpeg read timeout.")
            return None
        except Exception as e:
            logger.error(f"Error reading from FFmpeg: {e}")
            if self.on_error_callback:
                await self.on_error_callback("read_error")
            return None

    async def get_state(self) -> FFmpegState:
        """获取当前状态"""
        async with self._state_lock:
            return self.state

    async def restart(self) -> bool:
        """重启FFmpeg进程"""
        async with self._state_lock:
            if self.state == FFmpegState.RESTARTING:
                logger.warning("Restart already in progress.")
                return False
            self.state = FFmpegState.RESTARTING

        logger.info("Restarting FFmpeg...")

        try:
            await self.stop()
            await asyncio.sleep(1)  # short delay before restarting
            return await self.start()
        except Exception as e:
            logger.error(f"Error during FFmpeg restart: {e}")
            async with self._state_lock:
                self.state = FFmpegState.FAILED
            if self.on_error_callback:
                await self.on_error_callback("restart_failed")
            return False

    async def _drain_stderr(self):
        """处理FFmpeg错误输出"""
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                error_msg = line.decode(errors='ignore').strip()
                if error_msg:
                    # 提升错误日志级别，便于调试
                    logger.warning(f"FFmpeg stderr: {error_msg}")
        except asyncio.CancelledError:
            logger.info("FFmpeg stderr drain task cancelled.")
        except Exception as e:
            logger.error(f"Error draining FFmpeg stderr: {e}")

    async def health_check(self) -> bool:
        """健康检查"""
        async with self._state_lock:
            if self.state != FFmpegState.RUNNING:
                return False
            
            if not self.process:
                return False
                
            # 检查进程是否还在运行
            if self.process.returncode is not None:
                logger.error(f"FFmpeg进程意外退出，返回码: {self.process.returncode}")
                self.state = FFmpegState.FAILED
                return False
                
            return True