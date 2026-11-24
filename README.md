# FrameAgent · 视频内容分析 Agent

视频分析工作台：上传视频，本地转写，再由大模型完成章节总结、自然语言检索和证据定位。当前仅分析语音，不理解视频画面。

## 核心功能与架构

- **自动转写**：faster-whisper 生成词级时间戳，也可导入 UTF-8 SRT/VTT 字幕。
- **总结与导航**：生成整体概括、章节总结和关键词证据，点击时间或高亮词回看。
- **智能检索**：按问题含义查找片段，返回回答、关联理由和原文依据；转写后即可使用，无需先生成总结。
- **执行记录**：展示工具调用、校验反馈和修正过程，支持恢复历史视频与成功结果。

```text
React + Vite → FastAPI → SQLite + 本地文件
                  ├─ 转写：FFmpeg → faster-whisper
                  ├─ 总结：读取全文 → 分章总结 → 校验 → 保存
                  └─ 检索：分批语义召回 → 读取上下文 → 校验 → 发布
```

总结和检索采用单 Agent 的 ReAct / Tool Calling 循环。程序检查结构、引用和时间，额外的模型调用校验内容依据；失败后反馈修正，通过后才保存。原话与播放时间均从真实转写片段解析。

检索由大模型分批理解完整转写实现，未使用向量数据库；长视频会增加耗时和调用次数。模型故障明确报错，不返回规则替代结果。详见[核心功能与系统架构](视频分析Agent_核心功能与系统架构.md)。

## 快速启动

需要 Python 3.10+、Node.js 18+，以及支持 `/chat/completions`、Function Calling 和 JSON 输出的 OpenAI 兼容模型服务。

首次配置：将 `.env.example` 复制为 `.env`，填写 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`（示例为 `deepseek-flash`）。`.env` 非空配置优先于同名环境变量，修改后需重启。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-asr.txt
.venv/bin/python scripts/prepare_asr.py
cd web
npm install
npm run build
cd ..
bash start.sh
```

打开 http://127.0.0.1:8765 。依赖、模型和前端构建就绪后，后续直接 `bash start.sh`。

语音模型默认 `base / CPU / int8`，从 ModelScope 下载至 `models/faster-whisper-base`；FFmpeg 由 `imageio-ffmpeg` 提供。`.env.example` 默认 `ASR_LANGUAGE=zh`，其他语种可修改或移除该项。仅使用字幕时，安装 `requirements.txt` 并跳过模型下载。

## 使用

1. 点击“导入视频”，上传并自动转写，也可附带字幕。推荐 H.264 MP4，首次演示建议使用 1～3 分钟清晰语音视频。
2. 转写就绪后，输入自然语言问题检索，或点击“生成总结”。
3. 点击章节时间、高亮词或检索证据跳转播放；展开“Agent 执行轨迹”查看过程。
4. 刷新后通过右上角“打开历史视频”恢复已有内容。

视频与数据保存在本地 `data/`，转写文本会发送给所配置的模型服务；总结、检索和校验产生对应服务费用。服务重启后未完成任务标记为失败，重新提交可复用已有转写。原型仅监听 `127.0.0.1`，暂不包含用户系统。