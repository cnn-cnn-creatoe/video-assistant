# 剪映视频自动化剪辑助手（Codex x Gemini CLI）

一个面向 Windows 的本地自动剪辑工作流：

- **Python** 生成剪映（JianyingPro/CapCut CN）草稿（draft），实现“可打开、可继续精修”的剪辑工程。
- **Gemini CLI** 负责多模态理解与结构化 JSON 输出（素材标签/字幕匹配/转场建议）。
- **faster-whisper** 负责本地转写音频生成带时间戳 SRT（隐私友好、离线可跑）。
- **ffmpeg/ffprobe** 负责抽音频、抽帧拼 storyboard（把视频变成可给模型看的图片代理）。
- **Codex（或任何 coding agent）** 负责把这些工具编排成一条可重复的流水线（你问一句，Agent 跑一套）。

核心代码在：`jianying-editor-skill/`。

## 能做什么

- 生成剪映草稿（导入视频/图片/音频、字幕、轨道编排）
- **视频音频分离**：从 mp4 抽取音频到独立音频轨（可选把视频静音）
- **播客/口播自动粗剪**：本地转写 SRT -> Gemini 匹配素材 -> 生成 Broll 轨（含转场兜底）
- 素材理解：图片 + 视频 storyboard -> `tags/desc`（结构化缓存，便于检索/匹配）
- 缓存复用：`.gemini_cache/`（不会提交到 git）

## 目录结构

- `jianying-editor-skill/`: 自动化核心（生成剪映草稿、资产索引、导出、示例）
- `.gemini_cache/`: 运行缓存（可删）
- `logs/`: 手工调试日志（可删）

## 环境要求（需要下载/安装）

### 必须

1. Windows 10/11
2. 剪映专业版（JianyingPro）已安装并至少启动一次（用于创建草稿目录）
3. Python 3.10+（建议 3.12）
4. `ffmpeg` 与 `ffprobe` 在 PATH 中可用
5. Node.js（用于 Gemini CLI）
6. Gemini CLI（已登录）
   - 安装示例：`npm i -g @google/gemini-cli`
   - 首次登录：终端运行 `gemini` 按提示完成授权

### Python 依赖（核心）

项目不强绑一大坨依赖，核心流程建议至少装：

```powershell
pip install -r requirements.txt
```

可选：
- `playwright`（如果要用 `web_recorder.py` 录网页动效）：`pip install playwright` 后执行 `playwright install chromium`

## 快速开始

### 草稿保存位置

默认草稿目录（剪映启动过一次后会自动生成）：

`%LOCALAPPDATA%\\JianyingPro\\User Data\\Projects\\com.lveditor.draft`

生成草稿后如果剪映列表不刷新，重启剪映通常即可看到新项目。

### 1) 视频音频分离（生成剪映草稿：视频轨 + 独立音频轨）

```powershell
python .\jianying-editor-skill\scripts\extract_audio_separate.py `
  --video "C:\path\to\input.mp4" `
  --project "AudioExtract_Demo" `
  --mute-video
```

### 2) 自动粗剪（不提供 SRT 则本地转写）

```powershell
python .\jianying-editor-skill\scripts\auto_edit_gemini_cli.py `
  --project "AutoEdit_Demo" `
  --main "C:\path\to\voice_or_video.mp4" `
  --materials "D:\path\to\materials_folder" `
  --style "口播/快剪" `
  --allow-reuse
```

### 3) 模型参数（默认优先 3 Pro）

`auto_edit_gemini_cli.py` 默认：
- 文本任务：`gemini-3-pro-preview`（`--gemini-text-model`）
- 视觉任务：`gemini-3-pro-preview`（`--gemini-vision-model`）

你也可以让 CLI 自动路由（不强制）：

```powershell
python .\jianying-editor-skill\scripts\auto_edit_gemini_cli.py `
  --project "AutoEdit_AutoModel" `
  --main "C:\path\to\voice_or_video.mp4" `
  --materials "D:\path\to\materials_folder" `
  --gemini-text-model auto `
  --gemini-vision-model auto
```

## 为什么用这些模型/组件

- **Gemini 3 Pro**：更稳定的结构化输出与长上下文推理，适合“字幕段落 -> 素材选择 -> 转场策略”这类需要一致性的任务，因此默认优先。
- **Flash/轻量模型**：更快更省，适合大量小请求或快速草稿，但在某些环境下可能对“看图”能力不稳定，所以脚本支持 `auto` 路由与回退逻辑。
- **faster-whisper（本地）**：Gemini CLI 在本环境下对 mp3/mp4 直接输入不稳定；本地 ASR 隐私更好、可离线运行、时间戳更可控。
- **storyboard（视频 -> 图片代理）**：把视频均匀抽帧拼成一张联系表，让视觉模型“看懂视频大意”，避开直接喂 mp4 的限制。

## 文档

- `jianying-editor-skill/VIDEO_ASSISTANT_INTEGRATION.md`: 本地集成改动与能力组合说明
- `jianying-editor-skill/scripts/README.md`: 脚本入口索引与用途

## 注意事项

- 本项目不会提供任何非官方软件获取方式；请使用合规渠道安装剪映与相关工具。
- `.gemini_cache/` 与 `logs/` 默认 gitignore，不会上传到仓库。

## 致谢 / 来源

本工作区的剪映草稿自动化能力基于开源社区项目做了本地集成与补充：

- 上游参考：`https://github.com/luoluoluo22/jianying-editor-skill`

如果你计划公开发布/商用，请先确认并遵循上游仓库的许可与署名要求。
