import json
import os
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("VIDEO_AGENT_URL", "http://127.0.0.1:8765")
VIDEO = Path(os.environ.get("VIDEO_AGENT_TEST_VIDEO", str(ROOT / "artifacts/live-upload-test.mp4")))


def main():
    if not VIDEO.is_file():
        raise SystemExit("请设置 VIDEO_AGENT_TEST_VIDEO 为清晰真人语音的本地 MP4 路径。")
    executable = os.environ.get("CHROMIUM_PATH")
    if not executable:
        candidates = sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))
        executable = str(candidates[-1]) if candidates else None
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL)
        expect(page.get_by_role("heading", name="从上传一段视频开始")).to_be_visible()
        expect(page.locator(".model-label")).to_contain_text("deepseek-flash")
        expect(page.get_by_role("combobox", name="Agent 模式")).to_have_count(0)
        expect(page.get_by_text("本地规则演示", exact=False)).to_have_count(0)
        expect(page.get_by_role("button", name="加载示例", exact=True)).to_have_count(0)
        page.screenshot(path=str(artifacts / "upload-start.png"), full_page=True)
        page.get_by_role("button", name="导入视频", exact=True).click()
        page.get_by_label("选择视频文件").set_input_files(VIDEO)
        started = time.time()
        page.get_by_role("button", name="上传并自动转写", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible(timeout=30000)
        expect(page.get_by_role("heading", name=VIDEO.name, exact=True)).to_be_visible()
        page.wait_for_function("document.querySelector('.status-dot')?.textContent === '已完成' || document.querySelector('.status-dot')?.textContent === '未完成'", timeout=300000)
        expect(page.locator(".status-dot")).to_have_text("已完成")
        transcribe_seconds = round(time.time() - started, 1)
        print(f"自动转写完成：{transcribe_seconds} 秒", flush=True)
        video_id = page.get_by_label("切换已上传视频").input_value()
        with httpx.Client(trust_env=False) as client:
            video = client.get(f"{URL}/api/videos/{video_id}").json()
        assert video["source"] == "asr", video["source"]
        assert video["segments"]
        assert any(segment.get("words") for segment in video["segments"])
        page.get_by_role("tab", name="原始转写").click()
        expect(page.locator(".transcript-row")).to_have_count(len(video["segments"]))
        assert page.get_by_role("button", name="生成总结", exact=True).is_enabled()
        page.get_by_role("tab", name="章节总结").click()
        started = time.time()
        page.get_by_role("button", name="生成总结", exact=True).click()
        expect(page.locator(".status-dot")).to_have_text("执行中", timeout=30000)
        page.wait_for_function("document.querySelector('.status-dot')?.textContent !== '执行中'", timeout=300000)
        expect(page.locator(".status-dot")).to_have_text("已完成")
        expect(page.locator(".chapter").first).to_be_visible()
        analyze_seconds = round(time.time() - started, 1)
        print(f"真实模型分析完成：{analyze_seconds} 秒", flush=True)
        with httpx.Client(trust_env=False) as client:
            updated = client.get(f"{URL}/api/videos/{video_id}").json()
            latest = updated["latest_task"]
            assert latest["mode"] == "llm"
            assert updated["summary"]["mode"] == "llm"
            assert any(event["kind"] == "validation" and event["data"]["passed"] for event in latest["events"])
        query = os.environ.get("VIDEO_AGENT_TEST_QUERY", "视频主要讲了什么？请定位最相关的内容。")
        page.get_by_role("textbox", name="检索问题").fill(query)
        page.get_by_role("button", name="检索", exact=True).click()
        expect(page.locator(".search-progress")).to_be_visible(timeout=30000)
        page.wait_for_function("document.querySelector('.status-dot')?.textContent !== '执行中' && !document.querySelector('.search-progress')", timeout=300000)
        expect(page.locator(".status-dot")).to_have_text("已完成")
        with httpx.Client(trust_env=False) as client:
            search_task = client.get(f"{URL}/api/videos/{video_id}").json()["latest_search"]
        assert search_task["status"] == "completed", search_task["error"]
        assert search_task["result"]["mode"] == "semantic"
        matches = search_task["result"]["matches"]
        assert matches
        expect(page.locator(".search-result")).to_have_count(len(matches))
        page.locator(".search-result").first.click()
        page.wait_for_function("target => Math.abs(document.querySelector('video').currentTime - target) < 2", arg=matches[0]["start"])
        page.locator("video").evaluate("element => element.pause()")
        page.locator(".chapter .anchor").first.click()
        expect(page.locator(".evidence-box")).to_be_visible()
        page.locator("video").evaluate("element => element.pause()")
        chapter = updated["summary"]["chapters"][-1]
        page.locator(".chapter").last.locator(".timestamp").click()
        page.wait_for_function("target => Math.abs(document.querySelector('video').currentTime - target) < 2", arg=chapter["start"])
        page.locator("video").evaluate("element => element.pause()")
        if page.get_by_role("button", name="关闭提示").count():
            page.get_by_role("button", name="关闭提示").click()
        page.get_by_role("button", name="收起执行记录 ↑").click()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "live-upload-result.png"), full_page=True)
        page.reload()
        expect(page.get_by_role("heading", name="从上传一段视频开始")).to_be_visible()
        page.get_by_label("切换已上传视频").select_option(video_id)
        expect(page.locator(".chapter")).to_have_count(len(updated["summary"]["chapters"]))
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "live-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        report = {"status": "passed", "video_id": video_id, "filename": VIDEO.name, "transcribe_seconds": transcribe_seconds,
                  "analyze_seconds": analyze_seconds, "segments": len(video["segments"]),
                  "chapters": len(updated["summary"]["chapters"]), "search_query": query, "search_matches": len(matches),
                  "checks": ["空白工作区现场上传", "未提供字幕的本地语音识别", "词级时间戳", "deepseek-flash真实工具调用", "独立证据校验", "检索播放跳转", "关键词证据跳转", "章节跳转", "历史结果恢复", "移动端布局"]}
        (artifacts / "live-upload-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
