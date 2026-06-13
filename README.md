阿铭 Miles 翻译助手 — 源码便携版使用说明
=============================
目前仅支持 Windows 系统。
需要在 config.ini 中填写 DeepSeek API Key 与 火山引擎的 appid 与 token
DeepSeek API Key 获取地址：https://platform.deepseek.com/api_keys (复制 Key 进行填写即可)

火山引擎appid获取地址：https://console.volcengine.com/speech/app (创建应用记得勾选 语音合成大模型 与 豆包语音合成模型2.0)
火山一起拿token获取地址：https://console.volcengine.com/speech/service/10007 (滑动到最底部，在服务接口认证信息中将隐藏的 Access Token 复制然后填写即可)

正确填写完之后，双击 run.vbs 运行即可，运行成功右上角会有一个白色的悬浮球


🛠️ 首次使用（只需一次）
───────────────────────
双击 setup.bat
  → 自动下载 Python 到 portable_python/ 目录
  → 自动安装所有 pip 依赖库
  → 自动下载 FFmpeg 到 ffmpeg/ 目录
  → 总共下载约 60 MB（来源：python.org + gyan.dev）


🚀 日常使用
───────────
双击 run.vbs

  热键功能：
    Ctrl+0        → 复制选中文字 → 翻译 → 朗读 + 卡拉OK字幕
    Ctrl+Shift+Q  → 退出程序
    点击悬浮球    → 显示/隐藏翻译窗口



🐛 排错调试
───────────
双击 run.bat  （显示控制台日志，方便看报错）

📁 便携说明
───────────
整个文件夹复制到任何 Windows 电脑都能直接跑。
只要 config.ini 里填好两个 API Key：

  1. DeepSeek API Key（翻译引擎） — 国内服务，直连可用
  2. 火山引擎 TTS Token + AppID（语音朗读）— 国内服务，直连可用

📂 项目文件结构
───────────────
  hotkey_monitor.py    主程序
  config.ini           你的 API Key 配置
  setup.bat            首次一键配置（下载 Python + FFmpeg）
  run.vbs              静默启动（日常用这个）
  run.bat              调试模式启动（看日志用）
  requirements.txt     Python 依赖清单
  portable_python/     便携版 Python 解释器（setup 自动创建）
  ffmpeg/              FFmpeg 音视频工具（setup 自动下载）
  tts_cache/           语音朗读缓存（自动生成）
