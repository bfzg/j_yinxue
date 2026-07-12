"""
字幕生成模块 - 使用 faster-whisper 生成中文字幕 SRT 文件
"""
import time
from pathlib import Path
from typing import Optional


class SubtitleTranscriber:
    """使用 faster-whisper 生成字幕"""

    def __init__(
        self,
        model_size: str = "medium",
        language: str = "zh",
        device: str = "cpu",
        compute_type: str = "int8"
    ):
        """
        Args:
            model_size: Whisper 模型大小 (tiny/base/small/medium/large-v3)
            language: 语言代码 (zh=中文)
            device: 计算设备 (cpu/cuda, Mac 只能用 cpu)
            compute_type: 计算精度 (int8 最快最省内存)
        """
        self.model_size = model_size
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        """加载 Whisper 模型（首次调用时加载，会下载模型文件）"""
        if self._model is not None:
            return

        print(f"  [Whisper] 加载模型: {self.model_size} (首次会下载，约 {self._model_size_hint()})")

        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type
        )
        print(f"  [Whisper] 模型加载完成")

    def _model_size_hint(self) -> str:
        """模型大致大小提示"""
        sizes = {
            "tiny": "75MB",
            "base": "145MB",
            "small": "480MB",
            "medium": "1.5GB",
            "large-v3": "3GB",
        }
        return sizes.get(self.model_size, "未知")

    def transcribe(self, audio_path: str | Path, output_dir: str | Path) -> Optional[Path]:
        """
        从音频生成 SRT 字幕文件

        Args:
            audio_path: MP3 音频文件路径
            output_dir: SRT 输出目录

        Returns:
            SRT 文件路径，失败返回 None
        """
        audio_path = Path(audio_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        srt_path = output_dir / (audio_path.stem + ".srt")

        if srt_path.exists():
            print(f"  [跳过] 字幕已存在: {srt_path.name}")
            return srt_path

        if not audio_path.exists():
            print(f"  [错误] 音频文件不存在: {audio_path}")
            return None

        self._load_model()

        print(f"  [Whisper] 转录中: {audio_path.name}")
        start_time = time.time()

        try:
            segments, info = self._model.transcribe(
                str(audio_path),
                language=self.language,
                beam_size=5,
                vad_filter=True,        # 过滤静音段
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                )
            )

            # 生成 SRT 格式字幕
            srt_lines = []
            for i, segment in enumerate(segments, 1):
                srt_lines.append(self._format_srt_segment(
                    i,
                    segment.start,
                    segment.end,
                    segment.text.strip()
                ))

            srt_path.write_text(
                "\n".join(srt_lines),
                encoding="utf-8"
            )

            elapsed = time.time() - start_time
            duration = info.duration
            ratio = elapsed / duration if duration > 0 else 0

            print(f"  [Whisper] 完成: {srt_path.name}")
            print(f"           音频时长: {self._format_time(duration)}")
            print(f"           处理耗时: {self._format_time(elapsed)} (速度: {ratio:.1f}x)")

            return srt_path

        except Exception as e:
            print(f"  [错误] 转录失败: {e}")
            return None

    def transcribe_batch(self, audio_paths: list, output_dir: str | Path) -> list:
        """
        批量生成字幕

        Args:
            audio_paths: MP3 文件路径列表
            output_dir: SRT 输出目录

        Returns:
            成功生成的 SRT 文件路径列表
        """
        results = []
        total = len(audio_paths)

        for i, audio_path in enumerate(audio_paths, 1):
            print(f"\n[{i}/{total}] 转录: {Path(audio_path).name}")
            srt_path = self.transcribe(audio_path, output_dir)
            if srt_path:
                results.append(srt_path)

        return results

    @staticmethod
    def _format_time(seconds: float) -> str:
        """秒数 → HH:MM:SS 格式"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _format_srt_segment(index: int, start: float, end: float, text: str) -> str:
        """格式化 SRT 字幕段落"""
        def srt_timestamp(t: float) -> str:
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            ms = int((t - int(t)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        return (
            f"{index}\n"
            f"{srt_timestamp(start)} --> {srt_timestamp(end)}\n"
            f"{text}\n"
        )
