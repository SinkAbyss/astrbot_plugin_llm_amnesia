import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register


# --- 1. 数据结构定义 ---
@dataclass
class DeletedRecord:
    """用于存储被临时删除的对话记录"""
    messages: list[dict]       # 被删除的消息列表
    conversation_id: str       # 对话ID
    timestamp: datetime        # 删除时间
    round_count: int           # 累计删除的轮次数

@register(
    "llm_amnesia",
    "SinkAbyss",
    "当您不满意大模型的回复时，使用 /forget 指令，让它“忘记”最近的N轮对话，以便您重新提问并获得更好的回答。",
    "1.1.6",
    "https://github.com/SinkAbyss/astrbot_plugin_llm_amnesia"
)
class ForgetPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 存储结构：{unified_msg_origin: {user_id: DeletedRecord}}
        self.deleted_conversations: dict[str, dict[str, DeletedRecord]] = {}
        # 并发锁
        self.lock = asyncio.Lock()
        # 后台清理任务
        self.cleanup_task = asyncio.create_task(self.initialize_cleanup_task())
        logger.info("遗忘插件(最终修复版)已加载")

    async def initialize_cleanup_task(self):
        """初始化定期清理任务"""
        while True:
            try:
                await self.cleanup_expired_deletions()
                await asyncio.sleep(300)  # 每5分钟清理一次
            except asyncio.CancelledError:
                logger.info("清理任务被取消")
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"清理任务出错: {e}")
                await asyncio.sleep(60)

    async def cleanup_expired_deletions(self):
        """(并发安全) 清理过期的删除记录"""
        async with self.lock:
            current_time = datetime.now()  # noqa: DTZ005
            # 遍历副本以安全修改
            for unified_msg_origin, user_deletions in list(self.deleted_conversations.items()):
                for user_id, record in list(user_deletions.items()):
                    # 30分钟后过期
                    if current_time - record.timestamp > timedelta(minutes=30):
                        del user_deletions[user_id]
                        logger.info(f"清理过期删除记录: unified_msg_origin={unified_msg_origin}, user={user_id}")
                
                if not user_deletions:
                    del self.deleted_conversations[unified_msg_origin]

    @filter.on_llm_request()
    async def on_llm_request_cleanup(self, event: AstrMessageEvent, req: ProviderRequest):
        """(并发安全) 在AstrBot即将调用LLM前，自动清除可“反悔”的遗忘记录"""
        unified_msg_origin = event.unified_msg_origin
        user_id = event.get_sender_id()
        
        async with self.lock:
            # 如果用户发起了新的对话请求，之前的“反悔”机会就失效了，防止对话逻辑错乱
            if (unified_msg_origin in self.deleted_conversations and 
                user_id in self.deleted_conversations[unified_msg_origin]):
                
                del self.deleted_conversations[unified_msg_origin][user_id]
                if not self.deleted_conversations[unified_msg_origin]:
                    del self.deleted_conversations[unified_msg_origin]
                
                logger.info(f"用户 {user_id} 发起了新的LLM请求，自动清除之前的遗忘备份。")

    @filter.command("forget")
    async def forget_conversations(self, event: AstrMessageEvent, round_count: int = 1):
        """(并发安全 + 防崩 + 累加记录 + 脏数据修复) 遗忘指定数量的对话轮次"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            logger.info(f"forget指令 - 会话: {unified_msg_origin}, 轮次: {round_count}")

            if not 1 <= round_count <= 10:
                yield event.plain_result("遗忘轮次数必须在1到10之间 ❌")
                return

            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            if not curr_cid:
                yield event.plain_result("无法获取当前对话ID ❌")
                return
            
            conversation = await conv_mgr.get_conversation(unified_msg_origin, curr_cid)
            if not conversation:
                yield event.plain_result("无法获取对话对象 ❌")
                return

            # --- 2. 历史记录解析与脏数据修复 ---
            conversation_history = []
            try:
                if conversation.history:
                    conversation_history = json.loads(conversation.history)
                    # [脏数据修复]: 如果解析后还是字符串，说明之前被“双重序列化”了，再次解析
                    if isinstance(conversation_history, str):
                        logger.warning("检测到数据库存在双重序列化数据，正在自动修复...")
                        conversation_history = json.loads(conversation_history)
            except Exception as e:  # noqa: BLE001
                logger.error(f"历史记录解析严重错误: {e}")
                yield event.plain_result("对话历史格式异常，无法操作 ❌")
                return

            if len(conversation_history) < round_count * 2:
                yield event.plain_result(f"对话历史不足 {round_count} 轮 ❌")
                return
            
            # 查找切割点（倒序查找）
            split_index = len(conversation_history)
            rounds_found = 0
            for i in range(len(conversation_history) - 1, 0, -2):
                # 确保是字典类型
                msg_curr = conversation_history[i]
                msg_prev = conversation_history[i-1]
                
                if isinstance(msg_curr, dict) and isinstance(msg_prev, dict) and msg_curr.get("role") == "assistant" and msg_prev.get("role") == "user":
                    rounds_found += 1
                    if rounds_found == round_count:
                        split_index = i - 1
                        break
            
            if split_index == len(conversation_history):
                yield event.plain_result(f"只找到了 {rounds_found} 轮可遗忘的对话 ❌")
                return
            
            # 切割列表
            new_conversation_history = conversation_history[:split_index]
            deleted_messages = conversation_history[split_index:]

            # --- 3. 存入备份（支持累加） ---
            async with self.lock:
                if unified_msg_origin not in self.deleted_conversations:
                    self.deleted_conversations[unified_msg_origin] = {}
                
                existing_record = self.deleted_conversations[unified_msg_origin].get(user_id)
                
                if existing_record:
                    # 叠加逻辑：新删除的 + 旧删除的
                    merged_messages = deleted_messages + existing_record.messages
                    merged_round_count = round_count + existing_record.round_count
                    
                    self.deleted_conversations[unified_msg_origin][user_id] = DeletedRecord(
                        messages=merged_messages,
                        conversation_id=conversation.cid,
                        timestamp=datetime.now(),  # noqa: DTZ005
                        round_count=merged_round_count
                    )
                    logger.info(f"叠加遗忘记录：共 {merged_round_count} 轮")
                else:
                    # 首次删除
                    self.deleted_conversations[unified_msg_origin][user_id] = DeletedRecord(
                        messages=deleted_messages,
                        conversation_id=conversation.cid,
                        timestamp=datetime.now(),  # noqa: DTZ005
                        round_count=round_count
                    )

            # --- 4. 关键修复：直接传入列表 (List) ---
            # 移除了 json.dumps，防止双重序列化
            await conv_mgr.update_conversation(
                unified_msg_origin, conversation.cid, 
                history=new_conversation_history
            )
            
            logger.info(f"数据库已更新，删除后剩余消息数: {len(new_conversation_history)}")
            
            # --- 5. 文本提取与清洗 (防风控/防RAG刷屏) ---
            def safe_extract_text(content_obj):
                """从复杂结构中提取纯文本，过滤RAG/Memory等系统注入"""
                text_buffer = ""
                try:
                    if isinstance(content_obj, list):
                        for item in content_obj:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                raw_text = item.get('text', '')
                                # 核心过滤：如果包含RAG元数据，直接丢弃
                                if not any(k in raw_text for k in ["<RAG", "Memory>", "history_knowledge"]):
                                    text_buffer += raw_text
                    elif isinstance(content_obj, str):
                        text_buffer = content_obj
                    else:
                        text_buffer = str(content_obj)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"提取文本时发生错误: {e}")
                    return "[内容解析失败]"
                return text_buffer.strip()

            # 生成预览信息
            deleted_info = f"🗑️ 已删除 {round_count} 轮对话:\n\n"
            for i in range(0, len(deleted_messages), 2):
                if i+1 >= len(deleted_messages): break
                
                u_text = safe_extract_text(deleted_messages[i].get('content', ''))
                a_text = safe_extract_text(deleted_messages[i+1].get('content', ''))
                
                # 截断预览（只显示前50个字）
                u_show = u_text[:50].replace('\n', ' ') + ('...' if len(u_text)>50 else '')
                a_show = a_text[:50].replace('\n', ' ') + ('...' if len(a_text)>50 else '')
                
                deleted_info += f"第 {i//2 + 1} 轮:\n"
                deleted_info += f"👤: {u_show}\n"
                deleted_info += f"🤖: {a_show}\n\n"
            
            yield event.plain_result(
                f"{deleted_info}💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这些对话"
            )
            
        except Exception as e:  # noqa: BLE001
            logger.error(f"遗忘对话严重错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            yield event.plain_result(f"插件执行出错 ❌: {e}")

    @filter.command("cancel_forget")
    async def cancel_forget(self, event: AstrMessageEvent):
        """(并发安全) 取消遗忘操作，恢复被删除的对话"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            record_to_restore = None
            async with self.lock:
                if (unified_msg_origin in self.deleted_conversations and 
                    user_id in self.deleted_conversations[unified_msg_origin]):
                    record_to_restore = self.deleted_conversations[unified_msg_origin].pop(user_id)
                    if not self.deleted_conversations[unified_msg_origin]:
                        self.deleted_conversations.pop(unified_msg_origin)

            if not record_to_restore:
                yield event.plain_result("没有可恢复的遗忘记录 ❌")
                return
            
            conv_mgr = self.context.conversation_manager
            conversation = await conv_mgr.get_conversation(unified_msg_origin, record_to_restore.conversation_id)
            
            if conversation is None:
                yield event.plain_result("获取当前对话失败 ❌")
                return
            
            # 读取当前历史（包含脏数据自动修复）
            conversation_history = []
            if conversation and conversation.history:
                try:
                    conversation_history = json.loads(conversation.history)
                    if isinstance(conversation_history, str):
                         conversation_history = json.loads(conversation_history)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"恢复对话时解析历史记录失败: {e}")
                    yield event.plain_result("恢复对话时历史记录格式异常 ❌")
                    return
            
            # 拼接恢复
            restored_conversation_history = conversation_history + record_to_restore.messages
            
            # --- 6. 关键修复：直接传入列表 (List) ---
            await conv_mgr.update_conversation(
                unified_msg_origin, record_to_restore.conversation_id, 
                history=restored_conversation_history
            )
            
            logger.info(f"用户 {user_id} 恢复了 {record_to_restore.round_count} 轮对话")
            
            yield event.plain_result(
                f"✅ 已恢复 {record_to_restore.round_count} 轮被删除的对话\n\n对话已恢复到之前的状态"
            )
            
        except Exception as e:  # noqa: BLE001
            logger.error(f"取消遗忘时出错: {e}")
            yield event.plain_result(f"恢复对话时出现错误 ❌: {e}")

    @filter.command("forget_status")
    async def forget_status(self, event: AstrMessageEvent):
        """(并发安全) 查看遗忘状态"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            async with self.lock:
                record = self.deleted_conversations.get(unified_msg_origin, {}).get(user_id)
                if record:
                    time_ago = datetime.now() - record.timestamp  # noqa: DTZ005
                    minutes_ago = int(time_ago.total_seconds() / 60)
                    
                    yield event.plain_result(
                        f"📝 遗忘状态\n\n"
                        f"你有可恢复的遗忘记录:\n"
                        f"⏰ 删除时间: {minutes_ago}分钟前\n"
                        f"🔄 删除轮次: {record.round_count}轮\n"
                        f"💬 删除消息数: {len(record.messages)}条\n\n"
                        f"💡 发送 /cancel_forget 可以恢复这些对话"
                    )
                else:
                    yield event.plain_result("没有待恢复的遗忘记录 ✅")
                
        except Exception as e:  # noqa: BLE001
            logger.error(f"查看遗忘状态时出错: {e}")
            yield event.plain_result(f"查看状态时出错 ❌: {e}")

    @filter.command("forget_help")
    async def forget_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
📝 遗忘插件使用帮助

📋 基本指令:
• /forget - 遗忘最新一轮对话
• /forget 3 - 遗忘最新3轮对话

⚙️ 参数说明:
• 支持遗忘1-10轮对话
• 默认遗忘1轮对话
• 支持连续遗忘（如先忘1轮，再忘1轮，反悔时会恢复2轮）

🔄 其他指令:
• /cancel_forget - 取消遗忘，恢复对话
• /forget_status - 查看遗忘状态
• /forget_help - 显示此帮助信息

⏰ 注意事项:
• 删除记录30分钟后自动清理
• 反悔功能只能在下一条消息发送前使用
        """
        yield event.plain_result(help_text.strip())

    async def terminate(self):
        """插件卸载时的清理工作"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("遗忘插件已卸载")
