# jianying-editor-skill（本项目的剪映草稿自动化核心）

该目录提供通过 Python 代码生成/编辑剪映（JianyingPro/CapCut CN）草稿（draft）的能力，并在 `scripts/` 中集成了 Gemini CLI 的多模态理解与结构化输出。

如果你是从仓库根目录进入，请优先阅读根目录 `README.md`（包含完整安装与用法）。

## 关键入口

- `scripts/auto_edit_gemini_cli.py`
  - 自动粗剪主流程：本地转写 SRT（可选）+ 素材理解（图片/视频 storyboard）+ 字幕段落匹配素材 + 生成剪映草稿
- `scripts/extract_audio_separate.py`
  - 视频音频分离：抽取音频到独立音频轨，并生成剪映草稿
- `scripts/jy_wrapper.py`
  - `JyProject` 封装：写入剪映草稿目录、轨道/字幕/转场等能力

## 进一步文档

- `VIDEO_ASSISTANT_INTEGRATION.md`: 本工作区对该目录做的本地集成改动与能力组合说明
- `scripts/README.md`: 脚本索引

