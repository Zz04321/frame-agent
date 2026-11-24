import copy
import json

import pytest

from backend import agent, media, store
from backend.agent import Runtime, validate_structure
from backend.media import parse_subtitles, search_segments


def segment(segment_id, start, text, words=None):
    return {"segment_id": segment_id, "start": start, "end": start + 5, "text": text, "words": words or []}


def analysis_fixture(segments):
    summary = " ".join(segment["text"] for segment in segments)
    return {"overview": summary, "chapters": [{"title": "测试章节", "summary": summary,
            "segment_ids": [segment["segment_id"] for segment in segments],
            "anchors": [{"label": segments[0]["text"][:4], "evidence_segment_ids": [segments[0]["segment_id"]]}]}]}


@pytest.mark.parametrize("timecode, expected", [("00:20:01.00", 1201), ("01:00:00.00", 3600), ("25:00:00.00", 90000)])
def test_video_inspection_accepts_long_videos(monkeypatch, timecode, expected):
    from types import SimpleNamespace

    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stderr=f"Duration: {timecode}, start: 0.000000\nStream #0:0: Video: h264"))
    assert media.inspect_video("long-course.mp4") == expected


def test_video_inspection_rejects_zero_duration(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stderr="Duration: 00:00:00.00\nStream #0:0: Video: h264"))
    with pytest.raises(ValueError, match="有效的视频时长"):
        media.inspect_video("broken.mp4")


def test_upload_accepts_video_over_twenty_minutes(client, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "inspect_video", lambda path: 3600.0)
    response = client.post("/api/videos", files={"video": (
        "一小时课程.mp4", (store.ROOT / "assets/demo.mp4").read_bytes(), "video/mp4")})
    assert response.status_code == 200
    assert response.json()["duration"] == 3600.0
    assert response.json()["source"] == "pending"


def test_cross_segment_search_preserves_original_highlight():
    segments = [segment("s001", 0, "我们讨论梯度，"), segment("s002", 5, "下降。随后再用梯度下降。")]
    matches = search_segments(segments, "梯度 下降")
    assert len(matches) == 2
    assert matches[0]["segment_ids"] == ["s001", "s002"]
    assert matches[0]["text"][matches[0]["match_start"]:matches[0]["match_end"]] == "梯度，下降"
    assert matches[0]["start"] == 0
    assert matches[1]["start"] == 5
    assert matches[0]["precision"] == "segment"


def test_word_timestamps_and_normalization():
    segments = [segment("s001", 0, "讨论 ＡＩ!", [
        {"word": "讨论", "start": 1, "end": 2}, {"word": "AI", "start": 3, "end": 4},
    ])]
    result = search_segments(segments, "ai")[0]
    assert result["start"] == 3
    assert result["precision"] == "word"
    assert result["text"][result["match_start"]:result["match_end"]] == "ＡＩ"
    assert search_segments(segments, "没有这个词") == []
    with pytest.raises(ValueError):
        search_segments(segments, "！？")


def test_srt_vtt_parsing_and_time_errors():
    srt = "\ufeff1\r\n00:00:00,200 --> 00:00:03,000\r\n学习率\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n梯度下降\r\n"
    segments = parse_subtitles(srt, 10)
    assert len(segments) == 2
    assert segments[0]["start"] == 0.2
    vtt = "WEBVTT\n\n00:00.200 --> 00:03.000 align:start\n<b>学习率</b>\n"
    assert parse_subtitles(vtt, 10)[0]["text"] == "学习率"
    with pytest.raises(ValueError):
        parse_subtitles(srt, 1)
    with pytest.raises(ValueError):
        parse_subtitles("无时间戳的文本", 10)


def test_validator_rejects_missing_references_coverage_and_highlight():
    video = {"duration": 10, "segments": [segment("s001", 0, "梯度下降用于训练。"), segment("s002", 5, "学习率控制步长。") ]}
    draft = analysis_fixture(video["segments"])
    assert validate_structure(draft, video)["passed"]
    broken = copy.deepcopy(draft)
    broken["chapters"][0]["anchors"][0]["evidence_segment_ids"] = ["missing"]
    assert not validate_structure(broken, video)["passed"]
    broken = copy.deepcopy(draft)
    broken["chapters"][0]["segment_ids"].pop()
    assert any(issue["code"] == "coverage" for issue in validate_structure(broken, video)["issues"])
    broken = copy.deepcopy(draft)
    broken["chapters"][0]["anchors"][0]["label"] = "不在总结中的文字"
    assert not validate_structure(broken, video)["passed"]


def test_analysis_rejects_rule_mode_and_injected_errors(client):
    assert client.post("/api/videos/test-video/analyze", json={"mode": "local"}).status_code == 422
    assert client.post("/api/videos/test-video/analyze", json={"demonstrate_retry": True}).status_code == 422
    assert client.post("/api/demo").status_code in (404, 405)


def test_analysis_requires_configured_model(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/videos/test-video/analyze", json={})
    assert response.status_code == 400
    assert "模型密钥" in response.json()["detail"]


def test_save_gate_and_failed_revalidation_revoke_approval(client, monkeypatch):
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"passed": true, "issues": []}'})
    store.create_task("gate", "test-video", "llm")
    runtime = Runtime("gate", "test-video", "llm")
    assert "error" in runtime.execute("save_result", {})
    draft = analysis_fixture(store.video("test-video")["segments"])
    assert not runtime.execute("validate_result", {"result": draft})["passed"]
    runtime.execute("read_segments", {"offset": 0, "limit": 40})
    assert runtime.execute("validate_result", {"result": draft})["passed"]
    assert "error" not in runtime.execute("validate_result", {"result": {}})
    assert "error" in runtime.execute("save_result", {})
    assert store.video("test-video")["summary"] is None


def test_semantic_verifier_can_block_structurally_valid_result(client, monkeypatch):
    store.create_task("semantic", "test-video", "llm")
    runtime = Runtime("semantic", "test-video", "llm")
    runtime.execute("read_segments", {"offset": 0, "limit": 40})
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"passed": false, "issues": [{"message": "结论缺少依据"}]}'})
    report = runtime.execute("validate_result", {"result": analysis_fixture(store.video("test-video")["segments"])})
    assert not report["passed"]
    assert "error" in runtime.execute("save_result", {})


def test_llm_loop_uses_tool_feedback_and_retries(client, monkeypatch):
    valid = analysis_fixture(store.video("test-video")["segments"])
    invalid = copy.deepcopy(valid)
    invalid["chapters"][0]["anchors"][0]["evidence_segment_ids"] = ["missing"]
    calls = iter([
        ("get_video_info", {}), ("transcribe_video", {}),
        ("read_segments", {"offset": 0, "limit": 40}),
        ("validate_result", {"result": invalid}),
        ("validate_result", {"result": valid}), ("save_result", {}),
    ])
    observations = []

    def fake_completion(messages, tools=None, json_mode=False):
        if json_mode:
            return {"content": '{"passed": true, "issues": []}'}
        if messages[-1]["role"] == "tool":
            observations.append(json.loads(messages[-1]["content"]))
        name, arguments = next(calls)
        return {"role": "assistant", "content": None, "tool_calls": [{"id": name + str(len(messages)), "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    monkeypatch.setattr(agent, "completion", fake_completion)
    store.create_task("llm-test", "test-video", "llm")
    agent.run_task("llm-test", "test-video", "llm")
    assert store.task("llm-test")["status"] == "completed"
    assert any(item.get("passed") is False for item in observations)
    assert store.video("test-video")["summary"]["mode"] == "llm"


def test_structure_fixes_do_not_exhaust_content_validation(client, monkeypatch):
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"passed": true, "issues": []}'})
    store.create_task("separate-budgets", "test-video", "llm")
    runtime = Runtime("separate-budgets", "test-video", "llm")
    runtime.execute("read_segments", {"offset": 0, "limit": 40})
    valid = analysis_fixture(store.video("test-video")["segments"])
    invalid = copy.deepcopy(valid)
    invalid["chapters"][0]["anchors"][0]["evidence_segment_ids"] = ["missing"]
    for attempt in range(3):
        assert not runtime.execute("validate_result", {"result": invalid})["passed"]
    assert runtime.validations == 0
    assert runtime.execute("validate_result", {"result": valid})["passed"]
    assert runtime.validations == 1
    assert runtime.execute("save_result", {})["saved"]


def test_final_content_failure_explains_problem_and_blocks_save(client, monkeypatch):
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"passed": false, "issues": [{"message": "参数数量缺少原文依据"}]}'})
    store.create_task("explain-failure", "test-video", "llm")
    runtime = Runtime("explain-failure", "test-video", "llm")
    runtime.execute("read_segments", {"offset": 0, "limit": 40})
    draft = analysis_fixture(store.video("test-video")["segments"])
    for attempt in range(agent.MAX_VALIDATIONS):
        assert not runtime.execute("validate_result", {"result": draft})["passed"]
    with pytest.raises(ValueError, match="参数数量缺少原文依据"):
        runtime.check_validation_budget()
    assert "error" in runtime.execute("save_result", {})


def test_long_transcript_gets_enough_steps_to_read_all_pages(client, monkeypatch):
    segments = [segment(f"s{index + 1:03d}", index * 5, "学习率控制步长。") for index in range(809)]
    store.add_video("long-transcript", "长视频", store.ROOT / "assets/demo.mp4", 4050, "asr", segments)
    store.create_task("long-read", "long-transcript", "llm")
    runtime = Runtime("long-read", "long-transcript", "llm")
    draft = analysis_fixture(segments)
    draft["overview"] = "学习率控制步长。"
    draft["chapters"][0]["summary"] = "学习率控制步长。"
    sequence = [("read_segments", {"offset": offset, "limit": 40}) for offset in range(0, len(segments), 40)]
    sequence.extend([("validate_result", {"result": draft}), ("save_result", {})])
    sequence = iter(sequence)

    def fake_completion(messages, tools=None, json_mode=False):
        if json_mode:
            return {"content": '{"passed": true, "issues": []}'}
        name, arguments = next(sequence)
        return {"role": "assistant", "content": None, "tool_calls": [{"id": str(len(messages)), "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    monkeypatch.setattr(agent, "completion", fake_completion)
    result = agent.run_llm(runtime)
    assert len(runtime.read_ids) == 809
    assert result["mode"] == "llm"


def test_tool_schema_references_resolve_from_parameter_root():
    schema = agent.TOOLS[4]["function"]["parameters"]

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = schema
                for component in value["$ref"].split("/")[1:]:
                    target = target[component]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)


def test_upload_media_ranges_and_subtitle_errors(client):
    assets = store.ROOT / "assets"
    response = client.post("/api/videos", files={"video": ("课程.mp4", (assets / "demo.mp4").read_bytes(), "video/mp4"), "subtitles": ("课程.srt", (assets / "demo.srt").read_bytes(), "text/plain")})
    assert response.status_code == 200, response.text
    video = response.json()
    assert video["source"] == "subtitle"
    assert len(video["segments"]) == 12
    partial = client.get(video["media_url"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert len(partial.content) == 100
    assert client.get(f"/api/videos/{video['id']}/search", params={"q": "不存在的词"}).status_code in (404, 405)
    assert client.post(f"/api/videos/{video['id']}/search", json={"query": ""}).status_code == 422
    assert client.post("/api/videos", files={"video": ("bad.mp4", b"not-video", "video/mp4")}).status_code == 400
    assert client.get("/api/videos/missing").status_code == 404
    assert client.post(f"/api/videos/{video['id']}/subtitles", files={"subtitles": ("bad.srt", b"broken")}).status_code == 400


def test_duplicate_tasks_reuse_active_job_and_restart_preserves_transcript(client):
    assert store.create_task("first", "test-video", "llm") == "first"
    assert store.create_task("second", "test-video", "llm") == "first"
    store.initialize()
    assert store.task("first")["status"] == "failed"
    assert len(store.video("test-video")["segments"]) == 12


def test_transcribe_task_upload_without_subtitles_and_reuse(client, monkeypatch):
    assets = store.ROOT / "assets"
    uploaded = client.post("/api/videos", files={"video": ("现场上传.mp4", (assets / "demo.mp4").read_bytes(), "video/mp4")}).json()
    assert uploaded["source"] == "pending"
    assert uploaded["segments"] == []
    assert client.post(f"/api/videos/{uploaded['id']}/search", json={"query": "学习率的作用"}).status_code == 409
    calls = []

    def fake_transcribe(path, progress=None):
        calls.append(path)
        progress("识别语音", "正在处理音轨")
        return [segment("s001", 0, "学习率控制更新步长。")]

    monkeypatch.setattr(agent, "transcribe", fake_transcribe)
    started = client.post(f"/api/videos/{uploaded['id']}/transcribe").json()
    task = client.get("/api/tasks/" + started["task_id"]).json()
    assert task["status"] == "completed"
    assert task["mode"] == "transcribe"
    assert any(event["kind"] == "progress" for event in task["events"])
    ready = client.get(f"/api/videos/{uploaded['id']}").json()
    assert ready["source"] == "asr"
    assert ready["summary"] is None
    assert ready["segments"][0]["text"] == "学习率控制更新步长。"
    client.post(f"/api/videos/{uploaded['id']}/transcribe")
    assert len(calls) == 1


def test_transcribe_failure_keeps_video_retryable(client, monkeypatch):
    assets = store.ROOT / "assets"
    uploaded = client.post("/api/videos", files={"video": ("现场上传.mp4", (assets / "demo.mp4").read_bytes(), "video/mp4")}).json()

    def unavailable(path, progress=None):
        raise ValueError("语音模型暂不可用")

    monkeypatch.setattr(agent, "transcribe", unavailable)
    started = client.post(f"/api/videos/{uploaded['id']}/transcribe").json()
    task = client.get("/api/tasks/" + started["task_id"]).json()
    assert task["status"] == "failed"
    assert "语音模型暂不可用" in task["error"]
    assert client.get(f"/api/videos/{uploaded['id']}").status_code == 200
    assert store.active_task(uploaded["id"]) is None


def test_historical_rule_results_are_not_exposed(client):
    store.save_summary("test-video", {"mode": "local", "overview": "历史规则结果"})
    assert client.get("/api/videos/test-video").json()["summary"] is None
    store.add_video("demo", "历史示例", store.ROOT / "assets/demo.mp4", 124.1, "demo", [])
    assert all(video["source"] != "demo" for video in client.get("/api/videos").json())
    store.create_task("old-local", "test-video", "local")
    store.update_task("old-local", "completed")
    assert client.get("/api/videos/test-video").json()["latest_task"] is None
