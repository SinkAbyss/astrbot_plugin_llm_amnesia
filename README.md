# AstrBot 遗忘插件

[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 🎯 功能简介

当不满意LLM的回复时，可以使用指令从AstrBot的上下文管理器中删除最新一轮的对话，让大模型"遗忘"这段对话，然后用户重新发送问题，让LLM重新回复。

### ✨ 核心特性

- **🔮 智能遗忘**: 精准删除最新一轮用户-AI对话
- **↩️ 反悔功能**: 支持取消遗忘操作，恢复被删除的对话
- **⏰ 自动清理**: 自动清理过期的删除记录，防止内存泄漏
- **📊 状态查询**: 随时查看遗忘状态和可恢复的对话
- **🔒 会话隔离**: 按会话和用户隔离，互不影响

## 🚀 安装方法

### 方法一：通过插件市场安装（推荐）

1. 打开 AstrBot WebUI
2. 进入"插件管理"页面
3. 在插件市场中搜索"遗忘插件"
4. 点击安装即可

### 方法二：手动安装

1. 克隆本仓库到 AstrBot 插件目录
```bash
cd AstrBot/data/plugins
git clone https://github.com/your-username/astrbot_plugin_forget.git
```

2. 重启 AstrBot 或在 WebUI 中重载插件

## 📖 使用说明

### 指令列表

| 指令 | 描述 |
|------|------|
| `/forget` | 遗忘最新一轮对话 |
| `/cancel_forget` | 取消遗忘操作，恢复被删除的对话 |
| `/forget_status` | 查看遗忘状态和可恢复的对话 |

### 使用场景

#### 场景1：不满意AI回复时使用
```
用户: 帮我写一首关于春天的诗
AI: 春风拂面花自开，柳绿桃红映山崖...
用户: /forget  ← 遗忘这轮对话
AI: ✅ 已遗忘最新一轮对话
用户: 帮我写一首关于春天的诗  ← 重新提问
AI: 春光烂漫百花香，蝶舞莺飞乐未央...  ← 获得新的回复
```

#### 场景2：使用反悔功能
```
用户: 帮我写代码
AI: ```python\nprint("Hello World")\n```
用户: /forget  ← 不小心删除了
AI: ✅ 已遗忘最新一轮对话
用户: /cancel_forget  ← 立即反悔
AI: ✅ 已恢复被删除的对话  ← 成功恢复
```

### 使用限制

- **反悔时间限制**: 只能在下一条消息发送前取消遗忘
- **自动过期**: 删除记录30分钟后自动清理
- **会话隔离**: 不同会话、不同用户的删除记录互不影响

## 🔧 技术实现

### 核心原理

1. **对话管理**: 使用 AstrBot 的 `ConversationManager` 管理对话历史
2. **智能删除**: 精确识别并删除最新一轮完整的用户-AI对话
3. **临时存储**: 将被删除的对话临时存储在内存中
4. **自动清理**: 定期清理过期的删除记录

### 数据结构

```python
# 删除记录存储结构
deleted_conversations = {
    "session_id": {
        "user_id": ([deleted_messages], timestamp)
    }
}
```

### 对话轮次识别

插件会自动识别对话结构，找到最新一轮完整的对话：
- 用户消息 → AI回复（构成一轮完整对话）
- 只删除最末尾的完整对话轮次
- 保留其他历史对话内容

## 🛠️ 开发信息

### 插件结构

```
astrbot_plugin_forget/
├── main.py              # 主要插件代码
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 依赖库
├── README.md           # 使用说明
└── test_plugin.py      # 测试脚本
```

### 依赖要求

- AstrBot v3.0+
- Python 3.8+
- 无额外第三方依赖

### 开发接口

#### 主要类和方法

- `ForgetPlugin`: 主插件类
  - `forget_last_conversation()`: 遗忘最新对话
  - `cancel_forget()`: 取消遗忘操作
  - `forget_status()`: 查询遗忘状态
  - `cleanup_expired_deletions()`: 清理过期记录

#### 使用的 AstrBot API

- `self.context.conversation_manager`: 对话管理器
- `@filter.command()`: 指令过滤器
- `event.session_id`: 会话标识
- `event.get_sender_id()`: 用户标识

## 📝 更新日志

### v1.0.0 (2024-11-12)
- ✅ 基础遗忘功能
- ✅ 反悔功能
- ✅ 状态查询功能
- ✅ 自动清理机制
- ✅ 会话用户隔离

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发环境搭建

1. Fork 本仓库
2. 克隆到本地
3. 在 AstrBot 插件目录中创建符号链接
4. 开始开发

### 代码规范

- 使用类型注解
- 添加充分的注释
- 遵循 PEP 8 规范
- 添加异常处理

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🔗 相关链接

- [AstrBot 官方文档](https://docs.astrbot.app/)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin.html)
- [AstrBot 社区](https://github.com/AstrBotDevs/AstrBot)

## 💬 联系方式

如有问题或建议，欢迎：
- 提交 [Issue](https://github.com/your-username/astrbot_plugin_forget/issues)
- 加入 AstrBot 官方交流群
- 发送邮件至: your-email@example.com

---

⭐ 如果这个项目对你有帮助，请给个 Star 支持一下！