from celery import shared_task


@shared_task
def process_task(task_id):
    from carbot.minmax import structured_llm

    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "temperature": {"type": "number"},
            "condition": {"type": "string"}
        },
        "required": ["city", "temperature", "condition"]
    }

    result = structured_llm(
        prompt="What's the weather in San Francisco?",
        output_schema=schema
    )

    print(result)
    return {'task_id': task_id, 'status': 'completed'}


# =============================================================================
# CHAT MODE TASKS
# Each task: fetches chat + messages, calls appropriate LLM, stores response
# =============================================================================


@shared_task
def process_qna_task(chat_id, message_id, query):
    """
    Q&A Mode Task.

    Role: Answer specific questions about cars, features, specs, or explanations.
    The LLM answers based on the full chat history context.

    Steps:
    1. Fetch chat history from DB for context
    2. Build a prompt with chat history + the user's question
    3. Call structured_llm with a schema that returns an answer object
    4. Store the assistant's response as a Message in the DB

    Expected call:
        process_qna_task.delay(str(chat_obj.id), str(user_message.id), query)
    """
    import django.db
    from .models import Chat, Message
    from .prompts import build_chat_context, build_qna_prompt, QNA_SCHEMA
    from carbot.minmax import structured_llm

    # Step 1: Fetch all messages for this chat
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()

    # Step 2: Build combined chat history for context
    chat_context = build_chat_context(messages)

    # Close DB connections before LLM call (fixes SIGSEGV on Celery fork)
    django.db.close_old_connections()

    # Step 3: Build prompt and call LLM
    prompt = build_qna_prompt(chat_context, query)
    result = structured_llm(prompt=prompt, output_schema=QNA_SCHEMA)
    print(result)
    answer = result.get('text',result.get('answer', 'Sorry, I could not generate an answer.'))

    # Step 4: Store the response as an assistant message
    Message.objects.create(
        chat=chat_obj,
        sender=Message.Sender.ASSISTANT,
        message_type=Message.MessageType.PLAIN_TEXT,
        content={'text': answer}
    )

    return {'chat_id': chat_id, 'status': 'completed', 'answer': answer}


@shared_task
def process_comparison_task(chat_id, message_id, query):
    """
    Comparison Mode Task.

    Role: Compare two or more cars side-by-side and provide insights.
    The LLM identifies which cars are being compared and builds a comparison table.

    Steps:
    1. Fetch chat history for context
    2. Identify car names mentioned in the query
    3. Call structured_llm with comparison schema to generate a comparison table
    4. Store the comparison result as a Message (type=COMPARISON) in the DB

    Expected call:
        process_comparison_task.delay(str(chat_obj.id), str(user_message.id), query)
    """
    import django.db
    from .models import Chat, Message, Car
    from .prompts import (
        build_chat_context,
        build_car_extraction_prompt,
        build_comparison_prompt,
        build_car_details_str,
        CAR_EXTRACTION_SCHEMA,
        COMPARISON_SCHEMA,
    )
    from carbot.minmax import structured_llm

    # Step 1: Fetch chat history for context
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()
    chat_context = build_chat_context(messages)

    # Step 2: Use LLM to extract car names from query + chat history
    django.db.close_old_connections()
    extract_prompt = build_car_extraction_prompt(chat_context, query)
    extracted = structured_llm(prompt=extract_prompt, output_schema=CAR_EXTRACTION_SCHEMA)
    cars_to_compare = extracted.get('cars', [])

    if not cars_to_compare:
        Message.objects.create(
            chat=chat_obj,
            sender=Message.Sender.ASSISTANT,
            message_type=Message.MessageType.COMPARISON,
            content={'text': "I couldn't identify specific cars to compare from your query. Could you name the cars you'd like to compare?", 'type': 'no_cars_found'}
        )
        return {'chat_id': chat_id, 'status': 'no_cars_found'}

    # Step 3: Find matching cars in the DB
    found_cars = []
    for car_info in cars_to_compare:
        company = car_info.get('company', '')
        name = car_info.get('name', '')
        car = Car.objects.filter(
            company__icontains=company,
            name__icontains=name
        ).first()
        if car:
            found_cars.append(car)

    if len(found_cars) < 2:
        Message.objects.create(
            chat=chat_obj,
            sender=Message.Sender.ASSISTANT,
            message_type=Message.MessageType.COMPARISON,
            content={'text': f"I found only {len(found_cars)} of the {len(cars_to_compare)} cars you mentioned in my database. Could you check the car names?", 'type': 'insufficient_cars'}
        )
        return {'chat_id': chat_id, 'status': 'insufficient_cars'}

    # Step 4: Build car details string for comparison prompt
    car_details_str = build_car_details_str(found_cars)

    # Step 5: Use LLM to generate a comparison table
    django.db.close_old_connections()
    comparison_prompt = build_comparison_prompt(car_details_str, query)
    comparison = structured_llm(prompt=comparison_prompt, output_schema=COMPARISON_SCHEMA)
    print(comparison)

    # Step 6: Build readable comparison text for the user
    lines = ["# Car Comparison\n"]
    lines.append(f"## Summary\n{comparison.get('summary', '')}\n")

    categories = comparison.get('categories', [])
    if categories:
        lines.append("## Comparison by Category\n")
        for cat in categories:
            lines.append(f"### {cat.get('category', 'N/A')}")
            lines.append(f"**Winner: {cat.get('winner', 'N/A')}** — {cat.get('details', 'N/A')}\n")

    verdict = comparison.get('verdict', '')
    if verdict:
        lines.append(f"## Final Verdict\n{verdict}")

    comparison_text = "\n".join(lines)

    # Step 7: Store the comparison as a COMPARISON message
    Message.objects.create(
        chat=chat_obj,
        sender=Message.Sender.ASSISTANT,
        message_type=Message.MessageType.COMPARISON,
        content={
            'text': comparison_text,
            'type': 'comparison',
            'cars_compared': [f"{c.company} {c.name}" for c in found_cars],
            'raw_comparison': comparison
        }
    )

    return {'chat_id': chat_id, 'status': 'completed', 'cars_compared': len(found_cars)}


@shared_task
def process_retrieval_task(chat_id, message_id, query):
    """
    Retrieval Mode Task.

    Role: Find and recommend cars based on user needs/preferences.
    The LLM asks clarifying questions if needed, then ranks cars from
    the database and presents the best matches.

    Steps:
    1. Fetch chat history for context
    2. Analyze query to determine car requirements (budget, size, usage, etc.)
    3. If query is too vague, generate follow-up questions via LLM
    4. If enough info available, query the Car model DB and rank results
    5. Store response as Message (type=AGENT_QUESTION) in DB

    Expected call:
        process_retrieval_task.delay(str(chat_obj.id), str(user_message.id), query)
    """
    import django.db
    from .models import Chat, Message, Car
    from .prompts import (
        build_chat_context,
        build_filter_prompt,
        build_ranking_prompt,
        build_car_list_str,
        FILTER_SCHEMA,
        RANKING_SCHEMA,
    )
    from carbot.minmax import structured_llm

    # Step 1: Fetch chat history for context
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()
    chat_context = build_chat_context(messages)

    # Step 2: Use LLM to extract filters from query + chat history
    django.db.close_old_connections()
    filter_prompt = build_filter_prompt(chat_context, query)
    filters = structured_llm(prompt=filter_prompt, output_schema=FILTER_SCHEMA)

    # Step 3: If vague, ask follow-up questions
    if filters.get('is_vague', True):
        process_guidance_task.delay(chat_id, message_id, query)
        return {'chat_id': chat_id, 'status': 'delegated_to_guidance'}

    # Step 4: Build Django query from extracted filters
    cars = Car.objects.all()

    price_min = filters.get('price_min')
    price_max = filters.get('price_max')
    if price_min:
        cars = cars.filter(price__gte=price_min)
    if price_max:
        cars = cars.filter(price__lte=price_max)

    seats_min = filters.get('seats_min')
    seats_max = filters.get('seats_max')
    if seats_min:
        cars = cars.filter(seats__gte=seats_min)
    if seats_max:
        cars = cars.filter(seats__lte=seats_max)

    fuel_type = filters.get('fuel_type')
    if fuel_type:
        cars = cars.filter(fuel_type__icontains=fuel_type)

    horsepower_min = filters.get('horsepower_min')
    if horsepower_min:
        cars = cars.filter(horsepower__gte=horsepower_min)

    # Step 5: If more than 10 results, use LLM to rank top 10
    all_cars = list(cars[:20])  # fetch up to 20, let LLM pick top 10
    if not all_cars:
        Message.objects.create(
            chat=chat_obj,
            sender=Message.Sender.ASSISTANT,
            message_type=Message.MessageType.AGENT_QUESTION,
            content={'text': "I couldn't find any cars matching your criteria. Could you relax some of your requirements?", 'type': 'no_results'}
        )
        return {'chat_id': chat_id, 'status': 'no_results'}

    # Build car list string for LLM ranking
    car_list_str = build_car_list_str(all_cars)

    django.db.close_old_connections()
    ranking_prompt = build_ranking_prompt(chat_context, query, car_list_str, len(all_cars))
    ranked = structured_llm(prompt=ranking_prompt, output_schema=RANKING_SCHEMA)
    recommendations = ranked.get('recommendations', [])

    # Build response text
    if not recommendations:
        result_text = "Here are the cars I found:\n" + car_list_str
    else:
        lines = ["Here are the best cars I found for you:\n"]
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"{i}. **{rec['car']}** — {rec['reason']}")
        result_text = "\n".join(lines)

    # Step 6: Store the recommendation response
    Message.objects.create(
        chat=chat_obj,
        sender=Message.Sender.ASSISTANT,
        message_type=Message.MessageType.AGENT_QUESTION,
        content={'text': result_text, 'type': 'car_recommendations', 'filters_applied': filters}
    )

    return {'chat_id': chat_id, 'status': 'completed', 'recommendations_count': len(recommendations)}


@shared_task
def process_guidance_task(chat_id, message_id, query):
    """
    Guidance Mode Task.

    Role: Help users who are confused, overwhelmed, or don't know what they want.
    The LLM analyzes the full chat and asks relevant questions to help narrow
    down options or presents a guided discovery flow.

    Steps:
    1. Fetch full chat history
    2. Analyze what the user has shared so far (budget, preferences, etc.)
    3. Determine what key information is still missing
    4. Generate targeted questions to help the user clarify their needs
    5. Store questions as Message (type=AGENT_QUESTION) in DB

    Expected call:
        process_guidance_task.delay(str(chat_obj.id), str(user_message.id), query)
    """
    import django.db
    from .models import Chat, Message
    from .prompts import build_chat_context, build_guidance_prompt, GUIDANCE_SCHEMA
    from carbot.minmax import structured_llm

    # Step 1: Fetch full chat history
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()
    chat_context = build_chat_context(messages)

    # Step 2: Use LLM to analyze chat and generate targeted follow-up questions
    django.db.close_old_connections()
    guidance_prompt = build_guidance_prompt(chat_context, query)
    result = structured_llm(prompt=guidance_prompt, output_schema=GUIDANCE_SCHEMA)
    print(result)

    question = result.get('question', "What kind of car are you looking for?")
    what_we_know = result.get('what_we_know', '')
    what_we_need = result.get('what_we_need', '')

    # Step 3: Build friendly guidance response
    response_lines = []
    response_lines.append("Great question! Let me help you think through this.\n")

    if what_we_know:
        response_lines.append(f"**What I know so far:** {what_we_know}")

    if what_we_need:
        response_lines.append(f"**To find you the perfect car, I need to know:** {what_we_need}\n")

    response_lines.append(f"**{question}**")
    response_lines.append("\nTake your time — there are so many great options out there, and I want to help you find the exact right fit!")

    guidance_text = "\n".join(response_lines)

    # Step 4: Store the guidance response
    Message.objects.create(
        chat=chat_obj,
        sender=Message.Sender.ASSISTANT,
        message_type=Message.MessageType.AGENT_QUESTION,
        content={
            'text': guidance_text,
            'type': 'guidance',
            'question': question,
            'what_we_know': what_we_know,
            'what_we_need': what_we_need
        }
    )

    return {'chat_id': chat_id, 'status': 'completed', 'question': question}