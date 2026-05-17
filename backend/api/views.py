from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from .models import Chat, Message
from .sse import get_stream_messages, get_stream_timestamp, json_dumps
import json
import time

from .tasks import process_task, process_qna_task, process_comparison_task, process_retrieval_task, process_guidance_task


@csrf_exempt
@require_http_methods(["POST"])
def trigger_task(request):
    task_id = request.POST.get('task_id', 'unknown')
    result = process_task.delay(task_id)
    return JsonResponse({'task_id': result.id, 'status': 'Task queued'})


@csrf_exempt
@require_http_methods(["POST"])
def chat(request):
    """
    Main chat endpoint. Takes query and chat_id, stores message,
    calls LLM to classify intent, then dispatches to appropriate celery task.
    """
    data = json.loads(request.body)
    chat_id = data.get('chat_id')
    query = data.get('query')

    if not query:
        return JsonResponse({'error': 'Query is required'}, status=400)

    # Get or create chat
    if chat_id:
        try:
            chat_obj = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            chat_obj = Chat.objects.create(title=query[:50])
    else:
        chat_obj = Chat.objects.create(title=query[:50])

    # Store user message
    user_message = Message.objects.create(
        chat=chat_obj,
        sender=Message.Sender.USER,
        message_type=Message.MessageType.USER_INPUT,
        content={'text': query}
    )

    # =========================================================================
    # LLM CALL: Classify which mode/task to use
    # =========================================================================
    from carbot.minmax import structured_llm
    from .prompts import build_chat_context, build_classification_prompt, CLASSIFICATION_SCHEMA

    chat_history = Message.objects.filter(chat_id=chat_obj.id).order_by('created_at').all()
    chat_context = build_chat_context(chat_history)
    classification_prompt = build_classification_prompt(query, chat_context)

    classified_mode = structured_llm(
        prompt=classification_prompt,
        output_schema=CLASSIFICATION_SCHEMA
    )

    print(classified_mode)
    # =========================================================================
    # DISPATCH TO APPROPRIATE CELERY TASK BASED ON LLM CLASSIFICATION
    # =========================================================================
    selected_mode = classified_mode.get('selected_mode')

    # Dispatch to appropriate celery task based on LLM classification
    if selected_mode == 'qna':
        process_qna_task.delay(str(chat_obj.id), str(user_message.id), query)
    elif selected_mode == 'comparison':
        process_comparison_task.delay(str(chat_obj.id), str(user_message.id), query)
    elif selected_mode == 'retrieval':
        process_retrieval_task.delay(str(chat_obj.id), str(user_message.id), query)
    elif selected_mode == 'guidance':
        process_guidance_task.delay(str(chat_obj.id), str(user_message.id), query)

    return JsonResponse({
        'chat_id': str(chat_obj.id),
        'message_id': str(user_message.id),
        'classification': {
            'selected_mode': selected_mode,
            'reasoning': classified_mode.get('reasoning'),
            'confidence': classified_mode.get('confidence')
        },
        'status': 'Task dispatched based on LLM classification'
    })


# =============================================================================
# CHAT SIDEBAR & MESSAGING APIS
# =============================================================================

@csrf_exempt
@require_http_methods(["GET"])
def list_chats(request):
    """List all chats for the sidebar, ordered by most recent."""
    chats = Chat.objects.order_by('-updated_at').values('id', 'title', 'created_at', 'updated_at')
    return JsonResponse({'chats': list(chats)})


@csrf_exempt
@require_http_methods(["POST"])
def make_chat(_request):
    """Create a new chat entry with random title text."""
    import uuid
    random_id = str(uuid.uuid4())[:8]
    chat = Chat.objects.create(title=f"Chat {random_id}")
    return JsonResponse({'chat_id': str(chat.id), 'title': chat.title})


@csrf_exempt
@require_http_methods(["GET"])
def get_messages(_request):
    """Get all messages for a given chat_id (polling endpoint)."""
    chat_id = _request.GET.get('chat_id')
    if not chat_id:
        return JsonResponse({'error': 'chat_id is required'}, status=400)

    try:
        chat_obj = Chat.objects.get(id=chat_id)
    except Chat.DoesNotExist:
        return JsonResponse({'error': 'Chat not found'}, status=404)
    except Exception:
        return JsonResponse({'error': 'Invalid chat_id'}, status=400)

    messages = chat_obj.messages.order_by('created_at').values(
        'id', 'sender', 'message_type', 'content', 'created_at'
    )
    return JsonResponse({'messages': list(messages)})


@csrf_exempt
@require_http_methods(["GET"])
def stream_messages(request):
    """SSE endpoint: streams new messages for a chat in real-time using Redis polling."""
    chat_id = request.GET.get('chat_id')
    if not chat_id:
        return JsonResponse({'error': 'chat_id is required'}, status=400)

    try:
        chat_obj = Chat.objects.get(id=chat_id)
    except Chat.DoesNotExist:
        return JsonResponse({'error': 'Chat not found'}, status=404)
    except Exception:
        return JsonResponse({'error': 'Invalid chat_id'}, status=400)

    # After this id are new messages the client doesn't have yet
    last_id = request.GET.get('after_id')

    def event_stream():
        # Send current messages as initial event (from DB, since Redis stream only has new messages)
        messages = list(chat_obj.messages.order_by('created_at').values(
            'id', 'sender', 'message_type', 'content', 'created_at'
        ))
        yield f"data: {json_dumps({'type': 'init', 'messages': messages})}\n\n"
        # Track the last message id we've sent so we don't re-send on reconnect
        if messages:
            last_id = str(messages[-1]['id'])

        last_ts = get_stream_timestamp(chat_id) or 0.0
        if last_ts == 0.0:
            # No messages published yet — use DB timestamp as baseline
            last_ts = time.time()

        while True:
            try:
                time.sleep(0.4)
                current_ts = get_stream_timestamp(chat_id)
                if current_ts > last_ts:
                    last_ts = current_ts
                    new_messages = get_stream_messages(chat_id, after_id=last_id)
                    for msg in new_messages:
                        last_id = str(msg.get('id'))
                        yield f"data: {json_dumps({'type': 'message', 'message': msg})}\n\n"
            except (IOError, OSError):
                # Client disconnected — stop streaming gracefully
                break

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response