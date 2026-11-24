import json
import os
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("VIDEO_AGENT_URL", "http://127.0.0.1:8765")


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with httpx.Client(trust_env=False, timeout=30) as client:
        videos = client.get(URL + "/api/videos").json()
        selected = os.environ.get("VIDEO_AGENT_TEST_VIDEO_ID")
        if not selected:
            selected = next((video["id"] for video in videos if "10分30秒" in video["name"] and video["segment_count"]), None)
        if not selected:
            raise SystemExit("请先上传并转写10分钟视频，或用 VIDEO_AGENT_TEST_VIDEO_ID 指定已转写视频。")
        video = client.get(f"{URL}/api/videos/{selected}").json()
    query = os.environ.get("VIDEO_AGENT_TEST_QUERY", "老师用什么比喻解释人工智能的工作方式？")
    assert all(query not in segment["text"] for segment in video["segments"])
    executable = os.environ.get("CHROMIUM_PATH")
    if not executable:
        candidates = sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))
        executable = str(candidates[-1]) if candidates else None
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL)
        page.get_by_label("切换已上传视频").select_option(selected)
        expect(page.get_by_role("heading", name=video["name"], exact=True)).to_be_visible()
        expect(page.get_by_text("文字匹配", exact=True)).to_have_count(0)
        page.get_by_role("textbox", name="检索问题").fill(query)
        started = time.time()
        page.get_by_role("button", name="检索", exact=True).click()
        expect(page.locator(".search-progress")).to_be_visible(timeout=30000)
        page.wait_for_function("!document.querySelector('.search-progress') && document.querySelector('.status-dot')?.textContent !== '执行中'", timeout=300000)
        expect(page.locator(".status-dot")).to_have_text("已完成")
        expect(page.locator(".search-answer")).to_be_visible()
        with httpx.Client(trust_env=False, timeout=30) as client:
            updated = client.get(f"{URL}/api/videos/{selected}").json()
        task = updated["latest_search"]
        assert task["status"] == "completed", task["error"]
        result = task["result"]
        assert result["mode"] == "semantic"
        assert result["query"] == query
        assert result["matches"], result
        assert updated["summary"] == video["summary"], "检索不能覆盖已有总结"
        assert any(event["kind"] == "action" and event["title"] == "find_related_segments" for event in task["events"])
        assert any(event["kind"] == "validation" and event["data"]["passed"] for event in task["events"])
        first = result["matches"][0]
        page.locator(".search-result").first.click()
        page.wait_for_function("target => Math.abs(document.querySelector('video').currentTime - target) < 2", arg=first["start"])
        page.locator("video").evaluate("element => element.pause()")
        page.locator(".search-evidence").first.locator("summary").click()
        expect(page.locator(".search-evidence").first.locator("button").first).to_be_visible()
        page.locator(".search-evidence").first.locator("button").first.click()
        page.wait_for_function("target => Math.abs(document.querySelector('video').currentTime - target) < 2", arg=first["evidence"][0]["start"])
        page.locator("video").evaluate("element => element.pause()")
        if page.get_by_role("button", name="关闭提示").count():
            page.get_by_role("button", name="关闭提示").click()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "smart-search.png"), full_page=True)
        elapsed = round(time.time() - started, 1)
        print(f"自然语言检索成功：{len(result['matches'])} 处，{elapsed} 秒", flush=True)
        page.reload()
        page.get_by_label("切换已上传视频").select_option(selected)
        expect(page.get_by_role("textbox", name="检索问题")).to_have_value(query)
        expect(page.locator(".search-result")).to_have_count(len(result["matches"]))
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "smart-search-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1100})
        unrelated = "视频有没有讲红烧肉要放多少酱油、炖多长时间？"
        page.get_by_role("textbox", name="检索问题").fill(unrelated)
        page.get_by_role("button", name="检索", exact=True).click()
        expect(page.locator(".search-progress")).to_be_visible(timeout=30000)
        page.wait_for_function("!document.querySelector('.search-progress') && document.querySelector('.status-dot')?.textContent !== '执行中'", timeout=300000)
        expect(page.locator(".status-dot")).to_have_text("已完成")
        with httpx.Client(trust_env=False, timeout=30) as client:
            empty_task = client.get(f"{URL}/api/videos/{selected}").json()["latest_search"]
        assert empty_task["status"] == "completed", empty_task["error"]
        assert empty_task["result"]["matches"] == [], empty_task["result"]
        expect(page.locator(".search-result")).to_have_count(0)
        assert not errors, errors
        report = {"status": "passed", "video_id": selected, "query": query, "seconds": elapsed,
                  "answer": result["answer"], "matches": [{"title": match["title"], "start": match["start"]} for match in result["matches"]],
                  "unrelated_query": unrelated, "unrelated_answer": empty_task["result"]["answer"],
                  "checks": ["非原话自然语言检索", "真实模型语义召回", "原文查阅与独立校验", "结果和证据播放跳转", "结果恢复", "总结不被覆盖", "无相关内容不编造", "移动端布局"]}
        (artifacts / "smart-search-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
