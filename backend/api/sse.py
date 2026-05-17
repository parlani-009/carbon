import json
import time
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
    return redis.from_url(settings.REDIS_URL.replace('ssl_cert_reqs=CERT_NONE', ''), ssl_cert_reqs='none')


def get_stream_key(chat_id):
    return f"carbot:chat:{chat_id}:stream"


def get_timestamp_key(chat_id):
    return f"carbot:chat:{chat_id}:ts"


def publish_message(chat_id, message_data):
    """Append message to the Redis stream list and update the timestamp signal key."""
    client = get_redis_client()
    stream_key = get_stream_key(chat_id)
    ts_key = get_timestamp_key(chat_id)

    # Append as JSON to a Redis list (capped at 100 items to prevent unbounded growth)
    client.rpush(stream_key, json_dumps(message_data))
    client.ltrim(stream_key, -100, -1)
    # Update timestamp so SSE pollers detect the change
    client.set(ts_key, time.time())


def get_stream_messages(chat_id, after_id=None, limit=50):
    """
    Fetch new messages from the Redis stream that appeared after the client's last seen id.
    Returns a list of serialized message dicts.
    """
    client = get_redis_client()
    stream_key = get_stream_key(chat_id)

    # Get all items in the list
    raw_messages = client.lrange(stream_key, 0, -1)
    messages = []
    for raw in raw_messages:
        msg = json.loads(raw)
        # Filter by after_id if provided (after_id is the last message id the client already has)
        if after_id is not None:
            # after_id is the last processed message id
            if str(msg.get('id', '')) == str(after_id):
                # Everything after this is new
                messages = []
                continue
        messages.append(msg)

    # Return only the newest `limit` messages
    return messages[-limit:]


def get_stream_timestamp(chat_id):
    """Return the last update timestamp for a chat stream, or 0 if none."""
    client = get_redis_client()
    ts = client.get(get_timestamp_key(chat_id))
    return float(ts) if ts else 0.0


def serialize_message(msg):
    """Serialize a Message model instance to a dict for SSE transport."""
    return {
        'id': msg.id,
        'sender': msg.sender,
        'message_type': msg.message_type,
        'content': msg.content,
        'created_at': msg.created_at.isoformat() if msg.created_at else None,
    }