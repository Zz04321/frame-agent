import ctypes
import ctypes.util
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
TEXTS = [
    "今天我们用一个简单例子，理解梯度下降如何帮助模型学习。",
    "模型预测与真实答案之间的差距，可以用损失函数来衡量。",
    "训练的目标，是不断调整参数，让损失函数的数值逐渐减小。",
    "梯度下降沿着梯度的反方向更新参数，就像沿山坡走向谷底。",
    "接下来讨论学习率，它决定每次参数更新迈出多大的步子。",
    "学习率过大，可能在最低点附近来回震荡，甚至无法收敛。",
    "学习率过小，训练虽然更平稳，但到达较好结果需要更长时间。",
    "因此，使用梯度下降时，需要结合损失曲线调整学习率。",
    "最后，我们用损失曲线检查训练是否有效，不能只看运行次数。",
    "如果损失持续下降，通常说明参数更新正在改善训练目标。",
    "如果损失出现震荡，可以检查学习率，并核对数据和实现。",
    "总结一下，梯度下降负责更新方向，学习率控制步长，损失函数帮助评估效果。",
]


def narrator():
    library = ctypes.util.find_library("espeak-ng") or ctypes.util.find_library("espeak")
    if not library:
        raise RuntimeError("重新生成带中文语音的示例需安装 libespeak-ng；已附带的 assets/demo.mp4 可直接使用。")
    engine = ctypes.CDLL(library)
    engine.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    rate = engine.espeak_Initialize(2, 0, None, 0)
    engine.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    if engine.espeak_SetVoiceByName(b"cmn") != 0:
        raise RuntimeError("eSpeak 中文语音不可用")
    engine.espeak_SetParameter(1, 175, 0)
    chunks = []
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)

    @callback_type
    def callback(samples, count, events):
        if samples and count:
            chunks.append(ctypes.string_at(samples, count * 2))
        return 0

    engine.espeak_SetSynthCallback.argtypes = [callback_type]
    engine.espeak_SetSynthCallback(callback)
    engine._callback = callback
    engine.espeak_Synth.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

    def synthesize(text):
        chunks.clear()
        encoded = text.encode("utf-8")
        buffer = ctypes.create_string_buffer(encoded)
        status = engine.espeak_Synth(buffer, len(encoded) + 1, 0, 0, 0, 1, None, None)
        if status:
            raise RuntimeError(f"语音合成失败：{status}")
        return b"\x00\x00" * int(rate * 0.25) + b"".join(chunks) + b"\x00\x00" * int(rate * 0.55)

    return rate, synthesize


def font(size, chinese=True):
    choices = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "/usr/share/fonts/truetype/arphic/ukai.ttc"] if chinese else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for filename in choices:
        if Path(filename).exists():
            return ImageFont.truetype(filename, size)
    raise RuntimeError("需要中文字体才能重新生成示例视频。")


def draw_frame(index):
    frame = Image.new("RGB", (960, 540), "#182033")
    draw = ImageDraw.Draw(frame)
    for coordinate in range(0, 960, 40):
        draw.line((coordinate, 0, coordinate, 540), fill="#20283b")
    for coordinate in range(0, 540, 40):
        draw.line((0, coordinate, 960, coordinate), fill="#20283b")
    draw.rounded_rectangle((44, 32, 210, 66), 7, fill="#2d3150")
    draw.text((58, 39), "机器学习 · 微课堂", font=font(17), fill="#c5b8ed")
    draw.text((802, 41), "FRAME / 01", font=font(14, False), fill="#9099af")
    titles = ["梯度下降：找到下降的方向", "学习率：控制每一步的大小", "损失曲线：检查学习的效果"]
    draw.text((44, 96), titles[index // 4], font=font(32), fill="#f1edf9")
    draw.text((47, 146), "FROM A QUESTION TO UNDERSTANDING", font=font(11, False), fill="#8a91a7")
    points = []
    for position in range(120):
        horizontal = 105 + position * 6
        vertical = 350 - 120 * ((position - 70) / 70) ** 2
        points.append((horizontal, vertical))
    draw.line((80, 370, 825, 370), fill="#394158", width=2)
    draw.line((80, 198, 80, 370), fill="#394158", width=2)
    draw.line(points, fill="#a997ee", width=3)
    selected = min(110, 15 + index * 8)
    center = points[selected]
    draw.ellipse((center[0] - 9, center[1] - 9, center[0] + 9, center[1] + 9), fill="#d6c8ff", outline="#7057bd", width=3)
    draw.text((832, 362), "step", font=font(12, False), fill="#78839b")
    draw.rounded_rectangle((44, 406, 916, 496), 10, fill="#222b40")
    text = TEXTS[index]
    lines = [text[offset:offset + 32] for offset in range(0, len(text), 32)]
    for line_index, line in enumerate(lines):
        draw.text((65, 419 + line_index * 28), line, font=font(23), fill="#dfdeed")
    draw.text((47, 513), f"CHAPTER {index // 4 + 1:02d} / 03", font=font(10, False), fill="#8e8eac")
    draw.text((725, 513), "中文合成语音 · 示例视频", font=font(12), fill="#8e8eac")
    return frame


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def main():
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    rate, synthesize = narrator()
    segments = []
    pcm = bytearray()
    durations = []
    for index, text in enumerate(TEXTS):
        start = len(pcm) / (2 * rate)
        speech = synthesize(text)
        pcm.extend(speech)
        end = len(pcm) / (2 * rate)
        durations.append(end - start)
        segments.append({"segment_id": f"s{index + 1:03d}", "start": round(start, 3), "end": round(end - 0.15, 3), "text": text, "words": []})
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        narration = folder / "narration.wav"
        with wave.open(str(narration), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(pcm)
        manifest = []
        for index, duration in enumerate(durations):
            frame = folder / f"frame{index:02d}.png"
            draw_frame(index).save(frame)
            manifest.extend([f"file '{frame}'", f"duration {duration:.6f}"])
        manifest.append(f"file '{frame}'")
        listing = folder / "frames.txt"
        listing.write_text("\n".join(manifest) + "\n")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(narration), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", "-preset", "fast", "-crf", "24", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-shortest", str(assets / "demo.mp4")], check=True)
    (assets / "demo.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2) + "\n")
    subtitles = [f"{index + 1}\n{srt_time(segment['start'])} --> {srt_time(segment['end'])}\n{segment['text']}\n" for index, segment in enumerate(segments)]
    (assets / "demo.srt").write_text("\n".join(subtitles))
    print(f"示例已生成：{len(pcm) / (2 * rate):.1f} 秒，{len(segments)} 个片段。")


if __name__ == "__main__":
    main()
