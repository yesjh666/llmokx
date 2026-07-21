# LLMOKX 交易工具管理平台

基于 FastAPI 的独立 Web 管理平台，用于控制 LLM 分析、转发和通知功能。

## 功能特性

- **LLM 分析** — 直接调用大模型 API（非网关），支持动态添加 Prompt 规则/示例
- **意图转发** — Telegram Bot + Userbot + openclaw 多通道转发
- **双通道通知** — 微信 + Telegram 并行推送，互不遗漏
- **自动升级** — GitHub Releases 检查 + 下载 + 备份 + 回滚
- **独立开关** — 每个功能模块独立启用/禁用
- **结构化日志** — 按模块分文件，JSONL 操作记录，按天轮转

## 快速安装（一键脚本）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/yesjh666/llmokx/main/install.sh)
```

或手动：

```bash
git clone https://github.com/yesjh666/llmokx.git
cd llmokx
bash install.sh
```

## 手动部署

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 编辑配置
cp config/unified-config.json config/unified-config.json.bak
vim config/unified-config.json

# 4. 启动
python run.py
```

## 访问 Web 界面

启动后访问 `http://服务器IP:8080`

## 配置说明

配置文件: `config/unified-config.json`

```json
{
  "llm_analysis": {
    "enabled": true,
    "api_key": "sk-xxx",
    "model": "gpt-4o-mini"
  },
  "notification": {
    "wechat": { "enabled": true, "target": "..." },
    "telegram": { "enabled": true, "bot_token": "...", "chat_id": "..." }
  }
}
```

## API 文档

启动后访问 `http://服务器IP:8080/docs` 查看 Swagger 文档。

## 目录结构

```
llmokx/
├── app/
│   ├── api/            # FastAPI 路由
│   ├── core/           # 日志等核心模块
│   ├── services/       # 业务逻辑
│   └── static/         # Web 前端
├── config/             # 配置文件
├── logs/               # 日志（自动创建）
├── install.sh          # 一键安装脚本
├── uninstall.sh        # 卸载脚本
├── run.py              # 启动入口
└── requirements.txt
```

## License

MIT
