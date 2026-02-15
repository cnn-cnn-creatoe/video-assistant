# video-assistant 本地集成补充说明（本仓库内的改动与能力）

本文档说明在 `D:\agent\video-assistant\jianying-editor-skill` 这份代码上，我在本机工作区额外做了哪些改动/补充脚本，以及这些能力是如何“搭配组合”实现自动剪辑工作流的。

## 我在本仓库做了哪些改动

### 1) 修改（原有文件）

- `scripts/jy_wrapper.py`
  - 修复 `add_transition_simple()`：转场必须挂在“前一个片段”上，且轨道需至少 2 个片段才可添加（兼容 pyJianYingDraft 行为）。
  - 目的：让自动拼接 Broll 片段时的转场更稳定。

### 2) 新增（本地补充脚本/文档）

- `scripts/gemini_cli_bridge.py`
  - 作用：用 **非交互模式** 调用 `gemini` CLI（`-p/-m/-o json`），统一 PowerShell 引号、stdin 输入、大段 prompt 传参、重试与 JSON 解析。
- `scripts/media_storyboard.py`
  - 作用：用 `ffmpeg` 把视频生成 storyboard（联系表）图片，作为“视频视觉代理”给 Gemini 看。
  - 原因：Gemini CLI 在本环境下对 mp4/mp3 直接 `read_file` 容易受限，所以用“抽帧拼图”的方式喂给模型。
- `scripts/auto_edit_gemini_cli.py`
  - 作用：自动粗剪主流程（转写 SRT + 素材理解 + 字幕匹配素材 + 生成剪映草稿）。
  - 重要实现细节：
    - 用本地 `faster-whisper` 生成带时间戳 SRT（可选）。
    - 用 storyboard/图片缓存给 Gemini 生成 `tags/desc`（结构化 JSON）。
    - Gemini 输出匹配表后做**转场名与素材 id 清洗**，并提供“全 null”兜底分配，保证草稿可落地。
    - 默认优先使用 `gemini-3-pro-preview`（文本与视觉）。如需让 CLI 自动路由可传 `--gemini-*-model auto`。
- `scripts/extract_audio_separate.py`
  - 作用：对输入视频用 `ffmpeg` 抽取音频（mp3），并生成剪映草稿：视频轨 + 单独音频轨（可选把视频静音）。
- `scripts/README.md`
  - 作用：对 `scripts/` 下的入口脚本做“功能索引”，方便快速定位工具。

## 现在可以实现哪些功能（以及靠什么组合实现）

### A) “视频音频分离”并在剪映草稿中变成独立音频轨

能力：
- 输入一个 mp4
- 自动抽取音频为 mp3
- 生成剪映草稿：视频轨 + 单独音频轨（避免双声道可选把视频静音）

组合/依赖：
- `ffmpeg`：抽音频（`-vn -c:a libmp3lame ...`）
- `scripts/extract_audio_separate.py`：编排流程
- `scripts/jy_wrapper.py`（`JyProject`）：把视频/音频写入剪映草稿目录

### B) “文案/语音驱动”的自动粗剪（字幕对齐素材）

能力：
- 主文件（音频或视频）转写为带时间戳 SRT（本地，不走云）
- 多个素材（图片/视频）自动做“内容标签/描述”
- Gemini 根据字幕段落语义，把每一段匹配到最合适的素材
- 自动生成剪映草稿：字幕轨 + Broll 轨（按时间对齐）+ 转场（有兜底）

组合/依赖：
1. 本地转写（ASR）
   - `faster-whisper`（`scripts/auto_edit_gemini_cli.py` 内调用）
   - Windows Unicode 路径兼容：必要时先用 `ffmpeg` 抽 16k 单声道 wav 再转写（避免部分库对中文路径不兼容）
2. 视觉理解（素材打标）
   - `ffmpeg/ffprobe` + `scripts/media_storyboard.py`：视频 -> storyboard 图片
   - `scripts/gemini_cli_bridge.py`：调用 Gemini CLI 输出严格 JSON（`tags/desc`）
3. 文本匹配（字幕 -> 素材 id + 转场）
   - `scripts/gemini_cli_bridge.py`：调用 Gemini CLI 输出严格 JSON 数组
   - `scripts/auto_edit_gemini_cli.py`：对输出做结构校验、转场名清洗、id 合法性校验、全 null 兜底分配
4. 草稿生成
   - `scripts/jy_wrapper.py`：`JyProject` 写入剪映草稿目录（字幕轨、Broll 轨、转场等）

缓存与复用（避免重复花时间/花 token）：
- 缓存目录默认在工作区根目录的 `.gemini_cache/`（也可用 `--cache-dir` 指定）
  - `material_analysis.json`：素材分析缓存
  - `srt_matches.json`：字幕匹配缓存
  - `storyboards/`：视频 storyboard
  - `transcripts/`：转写 SRT

### C) Gemini “优先 3 Pro”策略

默认配置（在 `scripts/auto_edit_gemini_cli.py` 内）：
- 文本任务：`gemini-3-pro-preview`（`--gemini-text-model`）
- 视觉任务：`gemini-3-pro-preview`（`--gemini-vision-model`）

说明：
- 如果你想让 Gemini CLI 自动选模型（不强制），可传 `--gemini-text-model auto` 或 `--gemini-vision-model auto`。

## 常用命令（示例）

### 1) 音频分离

```powershell
python .\scripts\extract_audio_separate.py `
  --video "C:\path\to\input.mp4" `
  --project "AudioExtract_Demo" `
  --mute-video
```

### 2) 自动粗剪（不提供 SRT，则自动转写）

```powershell
python .\scripts\auto_edit_gemini_cli.py `
  --project "AutoEdit_Demo" `
  --main "C:\path\to\voice_or_video.mp4" `
  --materials "D:\path\to\materials_folder" `
  --style "口播/快剪"
```

### 3) 强制重新分析/匹配（忽略缓存）

```powershell
python .\scripts\auto_edit_gemini_cli.py `
  --project "AutoEdit_Demo" `
  --main "C:\path\to\voice_or_video.mp4" `
  --materials "D:\path\to\materials_folder" `
  --force
```

## 已知限制 / 注意事项

- Gemini CLI 在本环境下对 **mp4/mp3 直接喂入**并不稳定，因此使用“视频 storyboard 图片”作为视觉代理；音频转写优先走本地 `faster-whisper`。
- 自动导出（`auto_exporter.py`）依赖剪映前台界面状态，失败通常需要重启剪映或手动把剪映切到正确页面。
- 转场资产名称受剪映版本/素材库影响，脚本侧做了转场名兜底与模糊匹配，但仍可能出现“某些转场不存在”需要调整。

