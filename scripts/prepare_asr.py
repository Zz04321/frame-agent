import argparse
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]


def main():
    parser = argparse.ArgumentParser(description="为现场演示预下载本地语音识别模型")
    parser.add_argument("--model", choices=["tiny", "base", "small"], default="base")
    arguments = parser.parse_args()
    target = ROOT / "models" / ("faster-whisper-" + arguments.model)
    target.mkdir(parents=True, exist_ok=True)
    base = f"https://modelscope.cn/models/pengzhendong/faster-whisper-{arguments.model}/resolve/master"
    with httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=True) as client:
        for filename in FILES:
            path = target / filename
            if path.exists() and path.stat().st_size > 0:
                print(f"复用 {filename}", flush=True)
                continue
            temporary = path.with_suffix(path.suffix + ".part")
            print(f"下载 {filename}…", flush=True)
            try:
                with client.stream("GET", base + "/" + filename) as response:
                    response.raise_for_status()
                    size = 0
                    reported = 0
                    with temporary.open("wb") as destination:
                        for chunk in response.iter_bytes(1024 * 1024):
                            destination.write(chunk)
                            size += len(chunk)
                            if size - reported >= 20 * 1024 * 1024:
                                print(f"  {size / 1024 / 1024:.0f} MB", flush=True)
                                reported = size
                temporary.replace(path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
    print(f"模型准备完成：{target}", flush=True)


if __name__ == "__main__":
    main()
