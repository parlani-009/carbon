from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from .models import Chat, Message
import json

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
    # You are a GOD car salesperson. Analyze the customer's query and chat history
    # to decide which of the 4 modes best serves their need.
    # =========================================================================
    from carbot.minmax import structured_llm

    classification_schema = {
        "type": "object",
        "properties": {
            "selected_mode": {
                "type": "string",
                "enum": ["qna", "comparison", "retrieval", "guidance"],
                "description": "The mode that best fits the customer's query"
            },
            "reasoning": {
                "type": "string",
                "description": "Why this mode was selected"
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score for this classification"
            }
        },
        "required": ["selected_mode", "reasoning", "confidence"]
    }

    classification_prompt = f"""You are the world's best car salesperson with unmatched knowledge of all cars.
You have a customer asking about cars. Your job is to classify their query into ONE of these 4 modes:

1. **Q&A MODE** - Customer asks a specific question about cars, features, specs, or wants explanations.
   Example: "What is the difference between BMW and Mercedes?", "How fast is a Porsche 911?"

2. **COMPARISON MODE** - Customer wants to compare two or more cars side-by-side.
   Example: "Compare Tesla Model 3 vs BMW i4", "Which is better: Honda Civic or Toyota Corolla?"

3. **RETRIEVAL MODE** - Customer wants to find/recommend cars based on needs or preferences.
   They may ask questions like "What SUV should I buy for a family of 4?"

4. **GUIDANCE MODE** - Customer seems confused, overwhelmed, or doesn't know what they want.
   They might say things like "I don't know what car to get", "I'm confused about my options",
   or their query is vague and you'd need to ask follow-up questions to help them.

Analyze this customer's query AND their chat history, then classify which mode applies.

CUSTOMER QUERY: "{query}"

Chat History (for context):"""

    # Append chat history to prompt
    chat_history = Message.objects.filter(chat_id=chat_obj.id).order_by('created_at').all()
    for msg in chat_history:
        sender = "Customer" if msg.sender == "user" else "Assistant"
        if isinstance(msg.content, dict):
            text = msg.content.get('text', '')
        else:
            text = str(msg.content)
        classification_prompt += f"\n{sender}: {text}"

    classification_prompt += "\n\nReturn your classification in the specified JSON format."

    classified_mode = structured_llm(
        prompt=classification_prompt,
        output_schema=classification_schema
    )

    # Store the classified message
    # classified_message = Message.objects.create(
    #     chat=chat_obj,
    #     sender=Message.Sender.ASSISTANT,
    #     message_type=Message.MessageType.PLAIN_TEXT,
    #     content={
    #         'mode': 'classification',
    #         'query': query,
    #         'classified_mode': classified_mode  # Uncomment when LLM is implemented
    #     }
    # )
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
        # 'classified_message_id': str(classified_message.id),
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
