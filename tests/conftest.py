import json

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    with TestClient(app) as session:
        store.add_video("test-video", "测试课程", store.ROOT / "assets/demo.mp4", 124.1, "subtitle",
                        json.loads((store.ROOT / "assets/demo.json").read_text()))
        yield session
