import math
import re
import subprocess
import threading
import unicodedata
from pathlib import Path

import imageio_ffmpeg

ASR_LOCK = threading.Lock()
ASR_MODELS = {}


def inspect_video(path):
    process = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", process.stderr)
    if not match or "Video:" not in process.stderr:
        raise ValueError("无法读取视频，请上传有效的 MP4、WebM 或 MOV 文件。")
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法读取有效的视频时长，请检查视频文件是否完整。")
    return duration


def timestamp(value):
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def parse_subtitles(text, duration):
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(r"^\s*((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3}).*$")
    segments = []
    lines = text.splitlines()
    position = 0
    while position < len(lines):
        match = pattern.match(lines[position])
        position += 1
        if not match:
            continue
        content = []
        while position < len(lines) and lines[position].strip():
            content.append(re.sub(r"<[^>]*>", "", lines[position]).strip())
            position += 1
        start, end = map(timestamp, match.groups())
        if not 0 <= start < end <= duration + 0.15:
            raise ValueError("字幕时间无效或超出视频时长，请检查字幕与视频是否对应。")
        if segments and start < segments[-1]["start"]:
            raise ValueError("字幕需按时间先后排列。")
        if content:
            segments.append({"segment_id": f"s{len(segments) + 1:03d}", "start": start,
                             "end": min(end, duration), "text": " ".join(content), "words": []})
    if not segments:
        raise ValueError("未读取到带时间戳的字幕，请使用 UTF-8 编码的 SRT 或 VTT。")
    if sum(len(segment["text"]) for segment in segments) > 60000:
        raise ValueError("字幕内容过长，原型最多支持 60000 字符。")
    return segments


def normalized(text):
    characters = []
    positions = []
    for index, character in enumerate(text):
        for converted in unicodedata.normalize("NFKC", character).casefold():
            if converted.isalnum():
                characters.append(converted)
                positions.append(index)
    return "".join(characters), positions


def search_segments(segments, query):
    needle, _ = normalized(query)
    if not needle:
        raise ValueError("请输入至少一个汉字、字母或数字。")
    haystack = ""
    mapping = []
    for segment_index, segment in enumerate(segments):
        content, positions = normalized(segment["text"])
        word_times = []
        word_text = ""
        for word in segment.get("words", []):
            part, _ = normalized(word.get("word", word.get("text", "")))
            word_text += part
            word_times.extend([(word["start"], word["end"])] * len(part))
        exact_words = word_text == content and len(word_times) == len(content)
        for offset, original_index in enumerate(positions):
            times = word_times[offset] if exact_words else (segment["start"], segment["end"])
            mapping.append((segment_index, original_index, times, "word" if exact_words else "segment"))
        haystack += content
    results = []
    offset = 0
    while True:
        found = haystack.find(needle, offset)
        if found < 0:
            break
        first = mapping[found]
        last = mapping[found + len(needle) - 1]
        involved = segments[first[0]:last[0] + 1]
        original = "".join(segment["text"] for segment in involved)
        match_end = sum(len(segment["text"]) for segment in involved[:-1]) + last[1] + 1
        results.append({"start": first[2][0], "end": last[2][1], "text": original,
                        "match_start": first[1], "match_end": match_end,
                        "precision": first[3], "segment_ids": [segment["segment_id"] for segment in involved]})
        offset = found + 1
    return results


def asr_model_name():
    import os

    configured = os.environ.get("ASR_MODEL", "base")
    local = Path(__file__).resolve().parents[1] / "models" / ("faster-whisper-" + configured)
    return str(local) if (local / "model.bin").exists() else configured


def transcribe(path, progress=None):
    import os

    notify = progress or (lambda title, detail: None)

    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ValueError("未安装语音识别组件。请导入 SRT/VTT 字幕，或按 README 安装 faster-whisper 后重试。") from error
    audio = Path(path).with_suffix(".wav")
    try:
        notify("提取音频", "将视频音轨转换为 16 kHz 单声道音频。")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
                       capture_output=True, check=True, timeout=180)
        with ASR_LOCK:
            name = asr_model_name()
            device = os.environ.get("ASR_DEVICE", "cpu")
            compute = os.environ.get("ASR_COMPUTE_TYPE", "int8")
            key = (name, device, compute)
            if key not in ASR_MODELS:
                notify("加载语音模型", "使用本地模型；未缓存时需要联网下载。")
                try:
                    ASR_MODELS[key] = WhisperModel(name, device=device, compute_type=compute, cpu_threads=4)
                except Exception as error:
                    raise ValueError("语音模型加载失败。首次使用需要联网下载模型；可检查网络、将 ASR_MODEL 设置为已有模型目录，或导入 SRT/VTT 字幕继续演示。") from error
            notify("识别语音", "正在生成文字和词级时间戳，完成后即可检索。")
            language = os.environ.get("ASR_LANGUAGE") or None
            output, information = ASR_MODELS[key].transcribe(str(audio), word_timestamps=True, vad_filter=True,
                                                          beam_size=3, language=language,
                                                          initial_prompt="以下是普通话的简体中文转写。" if language == "zh" else None)
            segments = []
            last_reported = -10
            for item in output:
                if item.text.strip():
                    segments.append({"segment_id": f"s{len(segments) + 1:03d}", "start": item.start,
                                     "end": item.end, "text": item.text.strip(),
                                     "words": [{"word": word.word, "start": word.start, "end": word.end} for word in (item.words or [])]})
                    percent = min(99, round(item.end / max(information.duration, 0.01) * 100))
                    if percent - last_reported >= 10:
                        notify(f"转写进度 {percent}%", f"已识别 {len(segments)} 个片段。")
                        last_reported = percent
            if not segments:
                raise ValueError("没有识别到语音，可尝试导入字幕。")
            return segments
    except subprocess.CalledProcessError as error:
        raise ValueError("音频提取失败，请确认视频包含可读取的音轨。") from error
    finally:
        audio.unlink(missing_ok=True)
