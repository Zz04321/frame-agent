import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .agent import model_config, run_task
from .media import asr_model_name, inspect_video, parse_subtitles
from .models import AnalyzeRequest, SearchRequest
from .retrieval import run_search

for line in (store.ROOT / ".env").read_text().splitlines() if (store.ROOT / ".env").exists() else []:
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        setting = value.strip().strip("\"'")
        if setting:
            os.environ[name.strip()] = setting


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    yield


app = FastAPI(title="视频内容分析 Agent", lifespan=lifespan)


def require_video(video_id):
    video = store.video(video_id)
    if video is None:
        raise HTTPException(404, "视频不存在")
    return video


def public_video(video):
    output = {key: value for key, value in video.items() if key != "path"}
    if output.get("summary") and output["summary"].get("mode") != "llm":
        output["summary"] = None
    return output | {"media_url": f"/api/videos/{video['id']}/media"}


@app.get("/api/health")
def health():
    import importlib.util

    return {"status": "ok", "llm": model_config(), "asr_installed": importlib.util.find_spec("faster_whisper") is not None,
            "asr_ready": Path(asr_model_name()).is_dir()}


@app.get("/api/videos")
def list_videos():
    return [{key: value for key, value in public_video(video).items() if key not in ("segments", "summary")} | {"segment_count": len(video["segments"])} for video in store.videos() if video["source"] != "demo"]


@app.post("/api/videos")
async def upload_video(video: UploadFile = File(...), subtitles: UploadFile | None = File(None)):
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
        raise HTTPException(400, "请上传 MP4、WebM、MOV、M4V 或 MKV 视频。")
    video_id = uuid.uuid4().hex
    folder = store.DATA / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (video_id + suffix)
    try:
        with target.open("wb") as destination:
            while chunk := await video.read(1024 * 1024):
                destination.write(chunk)
        from starlette.concurrency import run_in_threadpool

        duration = await run_in_threadpool(inspect_video, target)
        segments = []
        if subtitles and subtitles.filename:
            content = await subtitles.read(4 * 1024 * 1024 + 1)
            if len(content) > 4 * 1024 * 1024:
                raise ValueError("字幕不能超过 4 MB。")
            segments = parse_subtitles(content.decode("utf-8-sig"), duration)
        store.add_video(video_id, Path(video.filename).name, target, duration, "subtitle" if segments else "pending", segments)
        return public_video(require_video(video_id))
    except (ValueError, UnicodeDecodeError) as error:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(error)) from error
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await video.close()
        if subtitles:
            await subtitles.close()


@app.get("/api/videos/{video_id}")
def get_video(video_id: str):
    output = public_video(require_video(video_id))
    output["latest_task"] = store.latest_task(video_id)
    output["latest_search"] = store.latest_search(video_id)
    return output


@app.get("/api/videos/{video_id}/media")
def media(video_id: str):
    video = require_video(video_id)
    return FileResponse(video["path"], media_type=mimetypes.guess_type(video["path"])[0] or "video/mp4")


@app.post("/api/videos/{video_id}/subtitles")
async def import_subtitles(video_id: str, subtitles: UploadFile = File(...)):
    video = require_video(video_id)
    if store.active_task(video_id):
        raise HTTPException(409, "分析正在执行，请完成后再替换字幕。")
    try:
        content = await subtitles.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError("字幕不能超过 4 MB。")
        segments = parse_subtitles(content.decode("utf-8-sig"), video["duration"])
        store.set_segments(video_id, segments, "subtitle")
        return public_video(require_video(video_id))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(400, str(error)) from error
    finally:
        await subtitles.close()


@app.post("/api/videos/{video_id}/search")
def search(video_id: str, request: SearchRequest, background_tasks: BackgroundTasks):
    video = require_video(video_id)
    if not video["segments"]:
        raise HTTPException(409, "还没有转写，请先导入字幕或运行分析以生成转写。")
    if not model_config()["configured"]:
        raise HTTPException(400, "未配置模型密钥，请在后端 .env 中配置。")
    task_id = uuid.uuid4().hex
    actual_id = store.create_task(task_id, video_id, "search")
    if actual_id != task_id:
        raise HTTPException(409, "当前视频有任务正在执行，请完成后再检索。")
    background_tasks.add_task(run_search, task_id, video_id, request.query)
    return {"task_id": actual_id}


@app.post("/api/videos/{video_id}/analyze")
def analyze(video_id: str, request: AnalyzeRequest, background_tasks: BackgroundTasks):
    require_video(video_id)
    if not model_config()["configured"]:
        raise HTTPException(400, "未配置模型密钥，请在后端 .env 中配置。")
    task_id = uuid.uuid4().hex
    actual_id = store.create_task(task_id, video_id, "llm")
    if actual_id == task_id:
        background_tasks.add_task(run_task, task_id, video_id, "llm")
    return {"task_id": actual_id}


@app.post("/api/videos/{video_id}/transcribe")
def prepare_transcript(video_id: str, background_tasks: BackgroundTasks):
    require_video(video_id)
    task_id = uuid.uuid4().hex
    actual_id = store.create_task(task_id, video_id, "transcribe")
    if actual_id == task_id:
        background_tasks.add_task(run_task, task_id, video_id, "transcribe")
    return {"task_id": actual_id}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = store.task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


dist = store.ROOT / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
