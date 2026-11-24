import copy
import json

import pytest

from backend import agent, retrieval, store


@pytest.fixture
def search_video(client):
    segments = [
        {"segment_id": "s001", "start": 0, "end": 5, "text": "训练时沿着坡度的反方向更新参数。", "words": []},
        {"segment_id": "s002", "start": 5, "end": 10, "text": "步子跨得太大，就会在谷底两边来回摇摆。", "words": []},
        {"segment_id": "s003", "start": 10, "end": 15, "text": "步子很小时，走到谷底就需要更长时间。", "words": []},
    ]
    store.add_video("semantic-video", "训练方法", store.ROOT / "assets/demo.mp4", 15, "subtitle", segments)
    return store.video("semantic-video")


def search_result():
    return {"answer": "视频用走路的步幅解释参数更新不稳定的原因。", "matches": [
        {"title": "更新步幅过大", "reason": "跨步太大时在谷底两侧摇摆，对应提问中的不稳定。", "segment_ids": ["s002"]},
    ]}


def mock_search_model(monkeypatch, result=None, with_invalid_reference=False, candidates=None):
    valid = result or search_result()
    calls = [("find_related_segments", {}), ("read_segments", {"offset": 0, "limit": 3})]
    if with_invalid_reference:
        invalid = copy.deepcopy(valid)
        invalid["matches"][0]["segment_ids"] = ["nonexistent"]
        calls.append(("validate_search_result", {"result": invalid}))
    calls.extend([("validate_search_result", {"result": valid}), ("save_search_results", {})])
    calls = iter(calls)
    feedback = []

    def fake_completion(messages, tools=None, json_mode=False):
        if json_mode:
            if "语义检索器" in messages[0]["content"]:
                return {"content": json.dumps({"candidates": candidates if candidates is not None else [
                    {"segment_ids": ["s002"], "reason": "更新步长过大导致震荡"}]}, ensure_ascii=False)}
            return {"content": '{"passed": true, "issues": []}'}
        if messages[-1]["role"] == "tool":
            feedback.append(json.loads(messages[-1]["content"]))
        name, arguments = next(calls)
        return {"role": "assistant", "content": None, "tool_calls": [{"id": str(len(messages)), "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}

    monkeypatch.setattr(agent, "completion", fake_completion)
    return feedback


def test_semantic_search_finds_paraphrase_and_repairs_reference(client, search_video, monkeypatch):
    query = "为什么学习率太高会让训练不稳定？"
    assert all(query not in segment["text"] for segment in search_video["segments"])
    feedback = mock_search_model(monkeypatch, with_invalid_reference=True)
    response = client.post("/api/videos/semantic-video/search", json={"query": query})
    assert response.status_code == 200
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "completed", task["error"]
    assert task["mode"] == "search"
    result = task["result"]
    assert result["mode"] == "semantic"
    assert result["query"] == query
    assert result["matches"][0]["start"] == 5
    assert result["matches"][0]["text"] == search_video["segments"][1]["text"]
    assert result["matches"][0]["evidence"][0]["segment_id"] == "s002"
    assert any(item.get("passed") is False for item in feedback)
    assert [event["data"]["passed"] for event in task["events"] if event["kind"] == "validation"] == [False, True]
    assert store.video("semantic-video")["summary"] is None
    assert client.get("/api/videos/semantic-video").json()["latest_search"]["result"] == result


def test_unrelated_question_returns_verified_empty_results(client, search_video, monkeypatch):
    mock_search_model(monkeypatch, result={"answer": "视频未涉及烹饪，未找到相关内容。", "matches": []}, candidates=[])
    response = client.post("/api/videos/semantic-video/search", json={"query": "红烧肉应该怎么做？"})
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "completed"
    assert task["result"]["matches"] == []
    assert "未找到" in task["result"]["answer"]
    assert any(event["kind"] == "validation" and event["data"]["passed"] for event in task["events"])


def test_search_refuses_unread_or_unverified_evidence(client, search_video, monkeypatch):
    store.create_task("read-gate", "semantic-video", "search")
    runtime = retrieval.SearchRuntime("read-gate", "semantic-video", "训练为什么不稳定")
    mock_search_model(monkeypatch)
    assert "error" in runtime.execute("save_search_results", {})
    report = runtime.execute("validate_search_result", {"result": search_result()})
    assert not report["passed"]
    runtime.execute("find_related_segments", {})
    report = runtime.execute("validate_search_result", {"result": search_result()})
    assert not report["passed"]
    assert any("read_segments" in issue["message"] for issue in report["issues"])
    runtime.execute("read_segments", {"offset": 0, "limit": 3})
    assert runtime.execute("validate_search_result", {"result": search_result()})["passed"]
    assert runtime.execute("save_search_results", {})["saved"]


def test_search_semantic_checker_rejects_unsupported_explanation(client, search_video, monkeypatch):
    store.create_task("content-gate", "semantic-video", "search")
    runtime = retrieval.SearchRuntime("content-gate", "semantic-video", "训练为什么不稳定")
    runtime.scanned = True
    runtime.candidates = []
    runtime.execute("read_segments", {"offset": 0, "limit": 3})
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"passed": false, "issues": [{"message": "解释与证据不一致"}]}'})
    assert not runtime.execute("validate_search_result", {"result": search_result()})["passed"]
    assert "error" in runtime.execute("save_search_results", {})


def test_search_model_failure_never_falls_back_to_literal_match(client, search_video, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("模型连接失败")

    monkeypatch.setattr(agent, "completion", unavailable)
    response = client.post("/api/videos/semantic-video/search", json={"query": "谷底"})
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "failed"
    assert "模型连接失败" in task["error"]
    assert task["result"] is None


def test_candidate_scan_rejects_fabricated_ids(client, search_video, monkeypatch):
    store.create_task("scan-gate", "semantic-video", "search")
    runtime = retrieval.SearchRuntime("scan-gate", "semantic-video", "训练不稳定")
    monkeypatch.setattr(agent, "completion", lambda *args, **kwargs: {"content": '{"candidates": [{"segment_ids": ["fake"], "reason": "相关"}]}'})
    assert "error" in runtime.execute("find_related_segments", {})
    assert not runtime.scanned
    assert runtime.saved is None


def test_search_request_validation_and_active_task_conflict(client, search_video, monkeypatch):
    assert client.post("/api/videos/semantic-video/search", json={"query": "  "}).status_code == 422
    assert client.post("/api/videos/semantic-video/search", json={"query": "问" * 501}).status_code == 422
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.post("/api/videos/semantic-video/search", json={"query": "训练方法"}).status_code == 400
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    store.create_task("in-progress", "semantic-video", "llm")
    assert client.post("/api/videos/semantic-video/search", json={"query": "训练方法"}).status_code == 409
    assert client.get("/api/videos/semantic-video/search", params={"q": "谷底"}).status_code in (404, 405)


def test_semantic_batches_cover_video_with_overlap():
    segments = [{"segment_id": str(index), "text": "内容"} for index in range(83)]
    batches = list(retrieval.transcript_batches(segments))
    assert len(batches) == 3
    assert {segment["segment_id"] for batch in batches for segment in batch} == {str(index) for index in range(83)}
    assert batches[1][0]["segment_id"] == "38"
