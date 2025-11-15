# 🧠 AstrBot 遗忘插件

!AstrBot Plugin [<sup>1</sup>](https://img.shields.io/badge/AstrBot-Plugin-blue.svg)
!Python [<sup>2</sup>](https://img.shields.io/badge/Python-3.8+-green.svg)
!License [<sup>3</sup>](https://img.shields.io/badge/License-MIT-yellow.svg)

## 🎯 功能简介

当不满意LLM的回复时，可以使用指令从AstrBot的上下文管理器中删除指定数量的对话轮次，让大模型"遗忘"这些对话，然后用户重新发送问题，让LLM重新回复。

### ✨ 核心特性

- **🔮 智能遗忘**: 支持删除1-10轮对话，精准控制遗忘范围。
- **↩️ 反悔功能**: 支持取消遗忘操作，恢复被删除的对话。
- **⏰ 自动清理**: 自动清理过期的删除记录，防止内存泄漏。
- **📊 状态查询**: 随时查看遗忘状态和可恢复的对话详情。
- **🔒 会话隔离**: 按会话和用户隔离，互不影响。
- **📚 帮助系统**: 完整的使用帮助和指令说明。
- **🚀 并发安全**: 引入异步锁机制，确保在高并发场景下的稳定运行，杜绝竞态条件。
- **🛠️ 健壮设计**: 采用事件钩子等高级特性，智能判断反悔时机，避免误操作。

## 🚀 安装方法

### 方法一：通过插件市场安装（推荐）

1.  打开 AstrBot WebUI。
2.  进入"插件管理"页面。
3.  在插件市场中搜索"遗忘插件" (llm_amnesia)。
4.  点击安装即可。

### 方法二：手动安装

1.  克隆本仓库到 AstrBot 插件目录：
    ```bash
    cd AstrBot/data/plugins
    git clone https://github.com/NigthStar/astrbot_plugin_llm_amnesia.git
    ```
2.  重启 AstrBot 或在 WebUI 中重载插件。

## 📖 使用说明

### 指令列表

| 指令             | 描述               | 示例           |
| ---------------- | ------------------ | -------------- |
| `/forget`        | 遗忘最新1轮对话    | `/forget`      |
| `/forget [n]`    | 遗忘最新n轮对话    | `/forget 3`    |
| `/cancel_forget` | 取消遗忘，恢复对话 | `/cancel_forget` |
| `/forget_status` | 查看遗忘状态       | `/forget_status` |
| `/forget_help`   | 显示帮助信息       | `/forget_help`   |

### 使用场景

#### 场景1：不满意AI回复时重新生成

```
用户: 帮我写一个Python函数来计算斐波那契数列
AI: (返回一个低效的递归实现...)
用户: /forget
AI: ✅ 已遗忘 1 轮对话...
用户: 帮我写一个Python函数来计算斐波那契数列，用迭代实现
AI: (返回一个高效的迭代实现...)
```

#### 场景2：批量遗忘多轮对话

```
用户: /forget 3
AI: ✅ 已遗忘 3 轮对话...
```

#### 场景3：使用反悔功能

```
用户: /forget  ← 误操作
AI: ✅ 已遗忘 1 轮对话...
用户: /cancel_forget
AI: ✅ 已恢复 1 轮被删除的对话...
```

#### 场景4：查看遗忘状态

```
用户: /forget_status
AI: 📝 遗忘状态

你有可恢复的遗忘记录:
⏰ 删除时间: 5分钟前
🔄 删除轮次: 3轮
💬 删除消息数: 6条
```

## 🔧 技术实现

本插件遵循 AstrBot 最佳实践，确保高效、稳定和可维护。

1.  **并发安全 (`asyncio.Lock`)**
    插件的后台清理任务和用户指令处理函数会并发访问共享的遗忘记录。为防止 `RuntimeError` 等竞态条件问题，我们引入了 `asyncio.Lock`：
    ```python
    self.lock = asyncio.Lock()

    # 所有对 self.deleted_conversations 的访问都被保护
    async with self.lock:
        # ... 安全地读取或修改字典 ...
    ```

2.  **智能反悔时机 (`@filter.on_llm_request`)**
    为修复“执行插件指令时意外清除遗忘记录”的Bug，插件不再监听所有新消息，而是采用更精准的事件钩子。只有当用户真正发起新一轮AI对话（即调用LLM）时，才会自动清除可反悔的记录。
    ```python
    @filter.on_llm_request()
    async def on_llm_request_cleanup(self, event: AstrMessageEvent, req: ProviderRequest):
        # ... 在这里执行清理逻辑，时机完美 ...
    ```

3.  **清晰的数据结构 (`dataclass`)**
    为提升代码可读性和可维护性，我们使用 `dataclass` 来替代复杂的元组，为遗忘记录提供清晰的结构定义：
    ```python
    @dataclass
    class DeletedRecord:
        """用于存储被临时删除的对话记录"""
        messages: List[dict]
        conversation_id: str
        timestamp: datetime
        round_count: int
    ```

4.  **高效的删除算法**
    `forget_conversations` 函数经过重构，使用单次反向循环和列表切片（slicing）操作来高效地分离和删除指定的对话轮次，避免了不必要的性能开销。

## 🛠️ 健壮性与安全

-   **并发安全**: 使用 `asyncio.Lock` 保护共享数据，杜绝竞态条件，防止后台任务崩溃。
-   **会话隔离**: 会话和用户完全隔离，互不影响。
-   **错误处理**: 完善的 `try...except` 块捕获和记录异常，防止插件意外退出。
-   **内存管理**: 后台任务会定时清理超过30分钟的遗忘记录，防止内存泄漏。

## 📚 开发信息

### 插件结构

```
astrbot_plugin_llm_amnesia/
├── main.py              # 插件核心逻辑
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 依赖库 (当前为空)
└── README.md            # 说明文档
```

### 依赖要求

-   AstrBot `v4.0.0`+ (为确保事件钩子等高级功能可用)
-   Python `3.8`+

## 📝 更新日志

### v1.1.5 (2025-11-15)

-   **🐛 Bug 修复**:
    -   修复了执行 `/forget_status` 等插件指令时，会意外清除可反悔记录的严重Bug。
-   **🔒 健壮性增强**:
    -   引入 `asyncio.Lock` 解决并发访问共享数据时的竞态条件问题，防止后台清理任务崩溃。
-   **✨ 代码质量**:
    -   使用 `dataclass` 替代复杂的元组来定义数据结构，大幅提升了代码的可读性和可维护性。
    -   重构了 `forget_conversations` 函数，使用单次循环和切片操作提升性能。

### v1.1.0 (2025-11-12)

-   ✅ 新增批量遗忘功能，支持1-10轮对话。
-   ✅ 新增 `/forget_help` 帮助指令。
-   ✅ 增强 `/forget_status` 状态显示。

### v1.0.0 (2025-11-12)

-   ✅ 基础遗忘功能 (`/forget 1`)。
-   ✅ 反悔功能 (`/cancel_forget`)。
-   ✅ 状态查询功能 (`/forget_status`)。

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发环境搭建

1.  Fork 本仓库。
2.  克隆到本地。
3.  在 AstrBot 的 `data/plugins` 目录中创建指向您本地仓库的符号链接，或直接克隆到该目录。
4.  开始开发，修改后在 AstrBot WebUI 重载插件即可看到效果。

### 代码规范

-   使用类型注解。
-   添加充分的注释和文档字符串。
-   遵循 PEP 8 规范。
-   添加健壮的异常处理。

## 📄 许可证

本项目采用 MIT 许可证。

## 🔗 相关链接

-   AstrBot 官方文档 [<sup>4</sup>](https://docs.astrbot.app/)
-   AstrBot 插件开发指南 [<sup>5</sup>](https://docs.astrbot.app/dev/star/plugin.html)

## 💬 联系方式

如有问题或建议，欢迎：
-   提交 Issue [<sup>6</sup>](https://github.com/NigthStar/astrbot_plugin_llm_amnesia/issues)
-   加入 AstrBot 官方交流群
-   发送邮件至: 1053757925@qq.com

---

⭐ 如果这个项目对你有帮助，请给个 Star 支持一下！

**享受你的遗忘插件吧！** 🧠✨
