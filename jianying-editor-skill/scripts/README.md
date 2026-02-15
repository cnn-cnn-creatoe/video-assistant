# scripts 目录索引

这里是 `jianying-editor-skill` 的常用脚本入口（按用途分类）。如果你只想“能跑起来”，优先看下面两条：

- `auto_edit_gemini_cli.py`: 自动粗剪（转写/素材理解/匹配/生成剪映草稿）
- `extract_audio_separate.py`: 把视频音频分离成单独音频轨（生成剪映草稿）

## 自动剪辑 / Gemini

- `auto_edit_gemini_cli.py`
  - 功能：本地 faster-whisper 生成 SRT（可选）+ Gemini CLI 解析素材（图片/视频 storyboard）+ 字幕段落匹配素材 + 生成剪映草稿（含转场兜底）。
  - 说明：默认优先 `gemini-3-pro-preview`（文本与视觉）；传 `--gemini-*-model auto` 可让 CLI 自动路由。
- `gemini_cli_bridge.py`
  - 功能：用非交互模式调用 `gemini` CLI（`-p/-m/-o json`），处理 PowerShell 引号/重试/JSON 解析。
- `media_storyboard.py`
  - 功能：用 `ffmpeg` 为视频生成 storyboard（联系表）图片，供 Gemini 做“视觉理解”（避免直接喂 mp4）。

## 剪映草稿生成 / 导出

- `jy_wrapper.py`
  - 功能：核心封装（`JyProject`），提供导入素材、轨道、字幕、转场、特效、关键帧等 API，并落盘到剪映草稿目录。
- `extract_audio_separate.py`
  - 功能：对输入视频用 `ffmpeg` 抽取音频（mp3），并生成草稿：视频轨 + 单独音频轨（可选将视频静音）。
- `auto_exporter.py`
  - 功能：通过 `pyJianYingDraft` 的控制器调用剪映导出草稿（依赖剪映界面状态，失败时通常需要重启剪映）。
- `template_replacer.py`
  - 功能：复制一个草稿模板并替换文本（用于模板化批量生产的“文本注入”）。

## 资产检索 / 同步

- `asset_search.py`
  - 功能：在 `data/*.csv` 中搜索滤镜/转场/特效等资产标识（支持同义词扩展），输出可直接用于 API 的 identifier。
- `sync_jy_assets.py`
  - 功能：把剪映本地缓存的音乐（Cache/music + rp.db 元信息）同步到本项目的 `assets/jy_sync`，并生成索引 CSV。

## 智能辅助能力

- `smart_rough_cut.py`
  - 功能：示例“智能粗剪”流程：调用 antigravity-api-skill（如果存在）用 Gemini-3-Pro 分析视频，产出高光片段 JSON，再生成剪映草稿。
  - 备注：当前工作区没有 `antigravity-api-skill/`，该脚本需要你另外放入对应目录后才可用。
- `movie_commentary_builder.py`
  - 功能：读取“故事版 JSON”，自动切片、字幕遮罩、双轨原声增强，生成解说类草稿。
- `smart_zoomer.py`
  - 功能：根据录屏/事件采集的 `_events.json`（点击/移动），自动给片段加缩放与平移关键帧，适合产品演示类视频。
- `web_recorder.py`
  - 功能：用 Playwright 录制网页动效（等待 `window.animationFinished` 或超时），输出 webm/素材给剪映用。

## 工具 / 集成

- `api_validator.py`
  - 功能：快速自检（能否创建草稿、导入测试素材、写入字幕等）。
- `mcp_server.py`
  - 功能：启动一个 MCP Server，把“创建草稿/粗剪/资产搜索”等能力暴露给支持 MCP 的 Agent 使用。

