import json
import redis
from datetime import datetime
from django.conf import settings


class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def json_dumps(obj):
    return json.dumps(obj, cls=DateTimeEncoder)


def get_redis_client():
    return redis.from_url(settings.REDIS_URL.replace('ssl_cert_reqs=CERT_NONE',''),ssl_cert_reqs="none")


def publish_message(chat_id, message_data):
    """Publish a message to the SSE channel for a given chat."""
    client = get_redis_client()
    channel = f"carbot:chat:{chat_id}"
    client.publish(channel, json_dumps(message_data))


def subscribe_to_chat(chat_id):
    """Subscribe to the SSE channel for a given chat. Returns the pubsub object."""
    client = get_redis_client()
    channel = f"carbot:chat:{chat_id}"
    pubsub = client.pubsub()
    pubsub.subscribe(channel)
    return pubsub


def serialize_message(msg):
    """Serialize a Message model instance to a dict for SSE transport."""
    return {
        'id': msg.id,
        'sender': msg.sender,
        'message_type': msg.message_type,
        'content': msg.content,
        'created_at': msg.created_at.isoformat() if msg.created_at else None,
    }