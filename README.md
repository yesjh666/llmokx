# 🦞 LLMOKX 交易工具管理平台

基于 FastAPI 的 Telegram 交易信号自动化处理平台：监听群消息 → LLM 分析意图 → 转发到目标群 → 推送通知提醒。

## 功能特性

- **LLM 智能分析** — 调用大模型理解交易信号语义，提取结构化意图（开仓/平仓/止盈/止损等）
- **多模型故障转移** — 主模型 → fallback → 备用模型列表，自动切换，认证错误快速跳过
- **GLM（智谱AI）专属适配** — 自动 JWT Token 生成与刷新，支持 Coding Plan 和标准端点
- **意图转发** — Userbot + Bot API 双通道，支持多群组 + 指定话题（论坛 Topic）
- **双通道通知** — Server酱（微信）+ Telegram Bot 并行推送
- **Prompt 管理** — 可视化管理系统提示词、规则、示例，支持 AI 助手生成规则
- **学习中心** — 分析历史记录 + 人工纠错学习 + 自动生成规则入库
- **自动升级** — GitHub Releases 检查 + 下载 + 备份 + 回滚 + 自动重启 + 通知
- **话题监听** — 支持监听 Telegram 论坛群的指定话题
- **登录保护** — HTTP Basic Auth 认证
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

安装脚本会自动：创建虚拟环境 → 安装依赖 → 注册 systemd 服务 → 启动。

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

## 服务管理（systemd）

安装脚本会注册 systemd 服务，常用命令：

```bash
# 启动服务
systemctl start llmokx

# 停止服务
systemctl stop llmokx

# 重启服务（修改配置后执行）
systemctl restart llmokx

# 查看运行状态
systemctl status llmokx

# 设置开机自启
systemctl enable llmokx

# 取消开机自启
systemctl disable llmokx

# 查看实时日志
journalctl -u llmokx -f

# 查看最近100行日志
journalctl -u llmokx -n 100
```

也可以在 Web 界面的「升级管理」页点击「重启服务」按钮重启。

## 访问 Web 界面

启动后访问 `http://服务器IP:8080`

### 启用登录保护

1. 进入「系统设置」页
2. 勾选「启用登录认证」
3. 设置用户名和密码（默认 admin / admin123）
4. 保存

启用后所有页面和 API 都需要登录。关闭浏览器后凭证自动失效。

## 使用流程

### 1. 配置 Telegram Userbot（监听用）

消息处理页 → 连接配置：
1. 填入 `api_id`、`api_hash`（从 https://my.telegram.org 获取）
2. 填入手机号 → 点测试连接 → 输入验证码 → 完成 2FA（如有）
3. Userbot 账号需已加入要监听的群

### 2. 配置监听群

消息处理页 → 监听：
1. 添加群的 Chat ID + 备注名称
2. 如监听论坛话题，填入话题 ID（留空=全部话题）
3. 保存后启动监听

### 3. 配置 LLM 模型

LLM 分析配置页：
1. 填入 API Key、API Base、模型名称
2. 可添加备用模型（故障自动切换）
3. 测试连接确认可用

**GLM（智谱AI）用户**：
- 标准端点：`https://open.bigmodel.cn/api/paas/v4`（推荐，稳定无限制）
- Coding Plan 端点：`https://open.bigmodel.cn/api/coding/paas/v4`（有限制）
- 标准 Key（`{id}.{secret}` 格式）程序自动生成 JWT Token

### 4. 配置意图转发

消息处理页 → 转发：
1. 添加转发目标群（支持多个）
2. 每个目标可指定话题 ID
3. 保存配置

### 5. 配置通知

消息处理页 → 通知：
- **Telegram**：填入 Bot Token + Chat ID
- **微信（Server酱）**：填入 SendKey（从 https://sct.ftqq.com/sendkey 获取）

### 6. 管理 Prompt

Prompt 管理页：
- 编辑系统提示词（控制 LLM 分析行为）
- 添加/删除自定义规则和示例
- 使用 AI 助手对话生成规则

### 7. 纠错学习

学习中心：
1. 查看分析历史记录
2. 发现错误 → 点「纠错学习」
3. 选择预设意图模板 → 修改为正确结果
4. 系统自动生成规则 + 示例入库，越用越准

## 升级管理

升级管理页：
- **检查更新** → **立即升级** → 自动备份 → 自动重启 → 自动通知
- 升级前自动备份到 `backup/` 目录
- `config/`、`data/`、`logs/`、`venv/` 目录在升级时保留
- 可从备份回滚到历史版本

## 配置说明

主配置文件：`config/unified-config.json`

```json
{
  "llm_analysis": {
    "enabled": true,
    "api_key": "your-api-key",
    "api_base": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4-flash",
    "fallback_model": "glm-4-air",
    "max_retries": 2,
    "temperature": 0.3,
    "max_tokens": 2000,
    "timeout": 90,
    "backup_models": []
  },
  "forward": {
    "enabled": true,
    "targets": [
      {"channel": "telegram", "target": "-100xxx", "description": "信号群", "topic_id": null}
    ]
  },
  "notification": {
    "enabled": true,
    "wechat": {"enabled": true, "sendkey": "SCTxxx"},
    "telegram": {"enabled": true, "bot_token": "xxx", "chat_id": "-100xxx"}
  },
  "monitor": {
    "enabled": false,
    "chat_ids": ["-100xxx"],
    "chat_topics": {},
    "min_message_length": 5
  },
  "server": {
    "auth_enabled": false,
    "username": "admin",
    "password": "admin123"
  }
}
```

Prompt 配置文件：`config/prompts.json`（含系统提示词、规则、示例）

## API 文档

启动后访问 `http://服务器IP:8080/docs` 查看 Swagger 文档。

## 目录结构

```
llmokx/
├── app/
│   ├── api/            # FastAPI 路由（llm/forward/notification/monitor/update/...）
│   ├── core/           # 日志等核心模块
│   ├── services/       # 业务逻辑（llm_analyzer/forwarder/notifier/telethon/...）
│   └── static/         # Web 前端（HTML/CSS/JS）
├── config/             # 配置文件（升级时保留）
│   ├── unified-config.json    # 主配置
│   ├── prompts.json           # Prompt 配置
│   ├── .api_key               # API Key 独立存储
│   └── telegram_userbot.json  # Userbot 配置
├── backup/             # 升级备份（自动创建）
├── data/               # 运行数据（升级时保留）
├── logs/               # 日志（升级时保留）
├── venv/               # 虚拟环境（升级时保留）
├── install.sh          # 一键安装脚本
├── uninstall.sh        # 卸载脚本
├── run.py              # 启动入口
├── llmokx.service      # systemd 服务文件
└── requirements.txt
```

## License

MIT
