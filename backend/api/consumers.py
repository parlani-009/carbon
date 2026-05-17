import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Chat, Message
from .sse import json_dumps, serialize_message


class ChatStreamConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time chat message streaming.
    Replaces the SSE polling endpoint.
    """

    async def connect(self):
        self.chat_id = self.scope['url_route']['kwargs']['chat_id']
        self.group_name = f'chat_{self.chat_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send existing messages as initial event
        messages = await self.get_initial_messages()
        await self.send(text_data=json.dumps({
            'type': 'init',
            'messages': messages
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Client can send a message to signal they received up to a certain id
        # (for deduplication if needed)
        pass

    async def chat_message(self, event):
        """Handler for when a new message is broadcast to the group."""
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'message',
            'message': message
        }))

    @database_sync_to_async
    def get_initial_messages(self):
        try:
            chat_obj = Chat.objects.get(id=self.chat_id)
        except (Chat.DoesNotExist, ValueError):
            return []
        messages = chat_obj.messages.order_by('created_at').values(
            'id', 'sender', 'message_type', 'content', 'created_at'
        )
        return list(messages)


async def broadcast_new_message(chat_id, message_data):
    """
    Called from Celery tasks after storing a message.
    Broadcasts to the chat group so all connected clients receive it instantly.
    """
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    group_name = f'chat_{chat_id}'
    await channel_layer.group_send(
        group_name,
        {
            'type': 'chat_message',
            'message': message_data
        }
    )