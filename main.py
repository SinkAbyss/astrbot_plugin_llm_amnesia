from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
import asyncio
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import json
from dataclasses import dataclass

# --- 1. 数据结构优化：使用 Dataclass ---
@dataclass
class DeletedRecord:
    """用于存储被临时删除的对话记录"""
    messages: List[dict]
    conversation_id: str
    timestamp: datetime
    round_count: int

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
        # 优化：使用 DeletedRecord 类型
        self.deleted_conversations: Dict[str, Dict[str, DeletedRecord]] = {}
        # 优化：并发锁
        self.lock = asyncio.Lock()
        # 后台清理任务
        self.cleanup_task = asyncio.create_task(self.initialize_cleanup_task())
        logger.info("遗忘插件已加载并启动清理任务")

    async def initialize_cleanup_task(self):
        """初始化定期清理任务"""
        while True:
            try:
                await self.cleanup_expired_deletions()
                await asyncio.sleep(300)  # 每5分钟清理一次
            except asyncio.CancelledError:
                logger.info("清理任务被取消")
                break
            except Exception as e:
                logger.error(f"清理任务出错: {e}")
                await asyncio.sleep(60)

    async def cleanup_expired_deletions(self):
        """(并发安全) 清理过期的删除记录"""
        async with self.lock:
            current_time = datetime.now()
            # 遍历副本以安全修改
            for unified_msg_origin, user_deletions in list(self.deleted_conversations.items()):
                for user_id, record in list(user_deletions.items()):
                    # 优化：通过属性访问 timestamp
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
            if (unified_msg_origin in self.deleted_conversations and 
                user_id in self.deleted_conversations[unified_msg_origin]):
                
                del self.deleted_conversations[unified_msg_origin][user_id]
                if not self.deleted_conversations[unified_msg_origin]:
                    del self.deleted_conversations[unified_msg_origin]
                
                logger.info(f"用户 {user_id} 发起了新的LLM请求，自动清除遗忘记录。")

    @filter.command("forget")
    async def forget_conversations(self, event: AstrMessageEvent, round_count: int = 1):
        """(并发安全 + 防RAG崩溃) 遗忘指定数量的对话轮次"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            logger.info(f"forget指令开始 - 会话: {unified_msg_origin}, 用户: {user_id}, 轮次: {round_count}")

            if not 1 <= round_count <= 10:
                yield event.plain_result("遗忘轮次数必须在1到10之间 ❌")
                return

            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            if not curr_cid:
                logger.warning("无法获取当前对话ID")
                yield event.plain_result("无法获取当前对话ID ❌")
                return
            
            conversation = await conv_mgr.get_conversation(unified_msg_origin, curr_cid)
            if not conversation:
                logger.warning("无法获取对话对象")
                yield event.plain_result("无法获取对话对象 ❌")
                return

            conversation_history = []
            try:
                conversation_history = json.loads(conversation.history) if conversation.history else []
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {e}")
                yield event.plain_result("对话历史格式错误 ❌")
                return

            if len(conversation_history) < round_count * 2:
                yield event.plain_result(f"对话历史不足 {round_count} 轮，当前只有 {len(conversation_history)//2} 轮对话 ❌")
                return
            
            split_index = len(conversation_history)
            rounds_found = 0
            for i in range(len(conversation_history) - 1, 0, -2):
                if conversation_history[i].get("role") == "assistant" and conversation_history[i-1].get("role") == "user":
                    rounds_found += 1
                    if rounds_found == round_count:
                        split_index = i - 1
                        break
            
            if split_index == len(conversation_history):
                logger.info(f"没有找到足够的可删除对话轮次")
                yield event.plain_result(f"只找到了 {rounds_found} 轮可遗忘的对话 ❌")
                return
            
            new_conversation_history = conversation_history[:split_index]
            deleted_messages = conversation_history[split_index:]

            # 优化：使用 DeletedRecord 存储
            async with self.lock:
                if unified_msg_origin not in self.deleted_conversations:
                    self.deleted_conversations[unified_msg_origin] = {}
                self.deleted_conversations[unified_msg_origin][user_id] = DeletedRecord(
                    messages=deleted_messages,
                    conversation_id=conversation.cid,
                    timestamp=datetime.now(),
                    round_count=round_count
                )

            await conv_mgr.update_conversation(
                unified_msg_origin, conversation.cid, history=new_conversation_history
            )
            
            logger.info(f"删除对话: 原始长度 {len(conversation_history)} -> 新长度 {len(new_conversation_history)}")
            
            # --- 2. 关键修复：RAG 防崩溃清洗逻辑 ---
            def safe_extract_text(content_obj):
                """从复杂结构中安全提取纯文本，过滤 RAG/Memory 注入"""
                text_buffer = ""
                try:
                    if isinstance(content_obj, list):
                        for item in content_obj:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                raw_text = item.get('text', '')
                                # 核心过滤：如果包含 RAG 关键词，直接丢弃
                                if not any(k in raw_text for k in ["<RAG", "Memory>", "相关记忆", "history_knowledge"]):
                                    text_buffer += raw_text
                    elif isinstance(content_obj, str):
                        text_buffer = content_obj
                    else:
                        text_buffer = str(content_obj)
                except Exception:
                    return "[内容解析失败]"
                return text_buffer.strip()

            deleted_info = f"🗑️ 已删除 {round_count} 轮对话:\n\n"
            for i in range(0, len(deleted_messages), 2):
                if i+1 >= len(deleted_messages): break
                
                # 使用安全提取函数
                user_text = safe_extract_text(deleted_messages[i].get('content', ''))
                ai_text = safe_extract_text(deleted_messages[i+1].get('content', ''))
                
                # 安全截断和格式化
                u_show = user_text[:50].replace('\n', ' ') + ('...' if len(user_text)>50 else '')
                a_show = ai_text[:50].replace('\n', ' ') + ('...' if len(ai_text)>50 else '')
                
                deleted_info += f"第 {i//2 + 1} 轮:\n"
                deleted_info += f"👤: {u_show}\n"
                deleted_info += f"🤖: {a_show}\n\n"
            # --------------------------------------
            
            yield event.plain_result(
                f"{deleted_info}💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这些对话"
            )
            
        except Exception as e:
            # 3. 兜底修复：捕获所有异常，防止 LLM 接管
            logger.error(f"遗忘对话严重错误: {e}")
            import traceback
            logger.error(f"堆栈跟踪: {traceback.format_exc()}")
            yield event.plain_result(f"插件执行出错: {str(e)} ❌")

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
            
            # 优化：通过属性访问 Dataclass 字段
            conv_mgr = self.context.conversation_manager
            conversation = await conv_mgr.get_conversation(unified_msg_origin, record_to_restore.conversation_id)
            
            if conversation is None:
                yield event.plain_result("获取当前对话失败 ❌")
                return
            
            conversation_history = json.loads(conversation.history) if conversation.history else []
            restored_conversation_history = conversation_history + record_to_restore.messages
            
            await conv_mgr.update_conversation(
                unified_msg_origin, record_to_restore.conversation_id, history=restored_conversation_history
            )
            
            logger.info(f"用户 {user_id} 在会话 {unified_msg_origin} 取消了遗忘操作，恢复了 {record_to_restore.round_count} 轮对话")
            
            yield event.plain_result(
                f"✅ 已恢复 {record_to_restore.round_count} 轮被删除的对话\n\n对话已恢复到之前的状态"
            )
            
        except Exception as e:
            logger.error(f"取消遗忘时出错: {e}")
            yield event.plain_result(f"恢复对话时出现错误 ❌: {str(e)}")

    @filter.command("forget_status")
    async def forget_status(self, event: AstrMessageEvent):
        """(并发安全) 查看遗忘状态"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            async with self.lock:
                record = self.deleted_conversations.get(unified_msg_origin, {}).get(user_id)
                if record:
                    # 优化：通过属性访问 Dataclass 字段
                    time_ago = datetime.now() - record.timestamp
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
                
        except Exception as e:
            logger.error(f"查看遗忘状态时出错: {e}")
            yield event.plain_result(f"查看状态时出错 ❌: {str(e)}")

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
                pass  # 任务取消是正常行为
            logger.info("清理任务已取消")
        logger.info("遗忘插件已卸载")
