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
    from carbot.minmax import structured_llm

    # Step 1: Fetch all messages for this chat
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()

    # Step 2: Build combined chat history for context
    chat_context = ""
    for msg in messages:
        sender = "Customer" if msg.sender == Message.Sender.USER else "Assistant"
        if isinstance(msg.content, dict):
            text = msg.content.get('text', '')
        else:
            text = str(msg.content)
        chat_context += f"{sender}: {text}\n"

    # Step 3: Build the god car salesman prompt
    prompt = f"""You are the world's most knowledgeable and honest car salesperson.
You have been helping a customer in a conversation. Read the full chat history below
and answer the customer's latest question honestly and with deep car expertise.

Be specific, factual, and helpful. If you don't know something, say so instead of guessing.
If the question is vague, ask a clarifying question.

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

CUSTOMER'S QUESTION: "{query}"

Provide a clear, detailed, and honest answer:"""

    # Close DB connections before LLM call (fixes SIGSEGV on Celery fork)
    django.db.close_old_connections()

    # Step 4: Call structured_llm
    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The answer to the customer's question"
            }
        },
        "required": ["answer"]
    }

    result = structured_llm (prompt=prompt, output_schema=schema)
    print(result)
    answer = result.get('text', 'Sorry, I could not generate an answer.')

    # Step 5: Store the response as an assistant message
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
    from carbot.minmax import structured_llm

    # Step 1: Fetch chat history for context
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()

    chat_context = ""
    for msg in messages:
        sender = "Customer" if msg.sender == Message.Sender.USER else "Assistant"
        if isinstance(msg.content, dict):
            text = msg.content.get('text', '')
        else:
            text = str(msg.content)
        chat_context += f"{sender}: {text}\n"

    # Step 2: Use LLM to extract car names from query + chat history
    extract_schema = {
        "type": "object",
        "properties": {
            "cars": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string", "description": "Car company/brand (e.g., BMW, Toyota, Tesla)"},
                        "name": {"type": "string", "description": "Car model name (e.g., Model 3, Civic, 911)"}
                    },
                    "required": ["company", "name"]
                },
                "description": "List of car names mentioned or implied in the query"
            }
        },
        "required": ["cars"]
    }

    extract_prompt = f"""From the user's query and chat history, identify all cars being discussed or compared.
Return each car as company + model name.

Examples:
- "Tesla Model 3 vs BMW i4" → [{{"company": "Tesla", "name": "Model 3"}}, {{"company": "BMW", "name": "i4"}}]
- "Compare Honda Civic and Toyota Corolla" → [{{"company": "Honda", "name": "Civic"}}, {{"company": "Toyota", "name": "Corolla"}}]

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

USER QUERY: "{query}"

Extract the cars:"""

    django.db.close_old_connections()
    extracted = structured_llm(prompt=extract_prompt, output_schema=extract_schema)
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
    car_details = []
    for c in found_cars:
        details = (
            f"{c.company} {c.name}\n"
            f"  Price: ${c.price or 'N/A'} | HP: {c.horsepower or 'N/A'} | Torque: {c.torque or 'N/A'} Nm\n"
            f"  Top Speed: {c.total_speed or 'N/A'} km/h | 0-100: {c.acceleration or 'N/A'}s\n"
            f"  Engine: {c.engine or 'N/A'} ({c.engine_capacity or 'N/A'} CC)\n"
            f"  Fuel: {c.fuel_type or 'N/A'} | Seats: {c.seats or 'N/A'}"
        )
        car_details.append(details)

    car_details_str = "\n\n".join(car_details)

    # Step 5: Use LLM to generate a comparison table
    comparison_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Brief overall summary of the comparison"},
            "categories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Category name (e.g., Performance, Value, Features)"},
                        "winner": {"type": "string", "description": "Which car wins this category"},
                        "details": {"type": "string", "description": "Why this car wins"}
                    },
                    "required": ["category", "winner", "details"]
                }
            },
            "verdict": {"type": "string", "description": "Overall recommendation and which car wins overall"}
        },
        "required": ["summary", "categories", "verdict"]
    }

    comparison_prompt = f"""Compare these cars side-by-side in a structured format.
Be honest and objective. Mention strengths and weaknesses of each.

--- CARS TO COMPARE ---
{car_details_str}
--- END CARS ---

--- USER QUERY ---
{query}
--- END USER QUERY ---

Provide a structured comparison:"""

    django.db.close_old_connections()
    comparison = structured_llm(prompt=comparison_prompt, output_schema=comparison_schema)
    print(comparison)

    # Step 6: Build readable comparison text for the user
    lines = [f"# 🚗 Car Comparison\n"]
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
    from carbot.minmax import structured_llm

    # Step 1: Fetch chat history for context
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()

    chat_context = ""
    for msg in messages:
        sender = "Customer" if msg.sender == Message.Sender.USER else "Assistant"
        if isinstance(msg.content, dict):
            text = msg.content.get('text', '')
        else:
            text = str(msg.content)
        chat_context += f"{sender}: {text}\n"

    # Step 2: Use LLM to extract filters from query + chat history
    # Maps user preferences to Car model fields: price, seats, fuel_type, engine, etc.
    filter_schema = {
        "type": "object",
        "properties": {
            "price_min": {"type": "number", "description": "Minimum price in $"},
            "price_max": {"type": "number", "description": "Maximum price in $"},
            "seats_min": {"type": "integer", "description": "Minimum number of seats"},
            "seats_max": {"type": "integer", "description": "Maximum number of seats"},
            "fuel_type": {"type": "string", "description": "Preferred fuel type (petrol, diesel, electric, hybrid)"},
            "body_type": {"type": "string", "description": "Preferred body type (SUV, sedan, hatchback, coupe, etc.)"},
            "horsepower_min": {"type": "number", "description": "Minimum horsepower"},
            "usage": {"type": "string", "description": "Intended use (city, highway, off-road, family, sports, etc.)"},
            "is_vague": {"type": "boolean", "description": "True if the query is too vague to filter effectively"}
        }
    }

    filter_prompt = f"""Analyze this car search query and the chat history.
Extract concrete filters for searching a car database. If the query is too vague
or lacks enough information to filter effectively, set is_vague to true.

Car database fields available: company, name, engine, engine_capacity, horsepower,
total_speed, acceleration, price, fuel_type, seats, torque.

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

USER QUERY: "{query}"

Extract filters:"""

    django.db.close_old_connections()
    filters = structured_llm(prompt=filter_prompt, output_schema=filter_schema)

    # Step 3: If vague, ask follow-up questions
    if filters.get('is_vague', True):
        question_prompt = f"""The customer is looking for cars but their query is too vague to search effectively.
Based on the chat history, ask 3-4 specific questions to narrow down their needs.
Ask about: budget, usage, family size, preferred features, fuel preference, etc.

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

USER QUERY: "{query}"

Ask clear, specific follow-up questions:"""

        question_schema = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 3-4 follow-up questions"
                }
            },
            "required": ["questions"]
        }

        result = structured_llm(prompt=question_prompt, output_schema=question_schema)
        print(result)
        questions = result.get('questions', [])

        Message.objects.create(
            chat=chat_obj,
            sender=Message.Sender.ASSISTANT,
            message_type=Message.MessageType.AGENT_QUESTION,
            content={'text': '\n'.join(questions), 'type': 'follow_up_questions'}
        )
        return {'chat_id': chat_id, 'status': 'follow_up_questions_asked'}

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
    car_list_str = "\n".join([
        f"- {c.company} {c.name} | Price: ${c.price or 'N/A'} | Seats: {c.seats or 'N/A'} | "
        f"HP: {c.horsepower or 'N/A'} | Fuel: {c.fuel_type or 'N/A'} | "
        f"Top Speed: {c.total_speed or 'N/A'} km/h | Acceleration: {c.acceleration or 'N/A'}s"
        for c in all_cars
    ])

    ranking_prompt = f"""A customer is looking for cars. Based on their query and chat history,
rank these cars from best match to least match. For each car, explain briefly why they'd want it.

Return the top {min(10, len(all_cars))} cars with a short reason for each.

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

--- USER QUERY ---
{query}
--- END USER QUERY ---

--- CARS TO RANK ---
{car_list_str}
--- END CARS ---

Return your ranked list:"""

    ranking_schema = {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "car": {"type": "string", "description": "Car name (company + name)"},
                        "reason": {"type": "string", "description": "Why this car fits the customer's needs"}
                    },
                    "required": ["car", "reason"]
                }
            }
        },
        "required": ["recommendations"]
    }

    django.db.close_old_connections()
    ranked = structured_llm(prompt=ranking_prompt, output_schema=ranking_schema)
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
    from carbot.minmax import structured_llm

    # Step 1: Fetch full chat history
    chat_obj = Chat.objects.get(id=chat_id)
    messages = Message.objects.filter(chat_id=chat_id).order_by('created_at').all()

    chat_context = ""
    for msg in messages:
        sender = "Customer" if msg.sender == Message.Sender.USER else "Assistant"
        if isinstance(msg.content, dict):
            text = msg.content.get('text', '')
        else:
            text = str(msg.content)
        chat_context += f"{sender}: {text}\n"

    # Step 2: Use LLM to analyze chat and generate targeted follow-up questions
    # Ask 1 good question to help narrow down the user's needs
    guidance_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "One focused follow-up question to help narrow down the best car for this customer"
            },
            "what_we_know": {
                "type": "string",
                "description": "What we already know about the customer's preferences from the chat"
            },
            "what_we_need": {
                "type": "string",
                "description": "What key information is still missing to make a good recommendation"
            }
        },
        "required": ["question", "what_we_know", "what_we_need"]
    }

    guidance_prompt = f"""You are a thoughtful car buying guide helping a confused customer.
Analyze the chat history and the customer's current query. Determine what we already know
about their preferences and what's still missing.

Then ask ONE focused, specific question that will have the biggest impact on narrowing down
the right car for them. Don't ask generic questions — make it specific to what they've shared.

Be warm, friendly, and helpful. The question should feel like a natural next step in conversation.

--- CHAT HISTORY ---
{chat_context}
--- END CHAT HISTORY ---

CUSTOMER: "{query}"

Analyze and ask your question:"""

    django.db.close_old_connections()
    result = structured_llm(prompt=guidance_prompt, output_schema=guidance_schema)
    print(result)

    question = result.get('question', "What kind of car are you looking for?")
    what_we_know = result.get('what_we_know', '')
    what_we_need = result.get('what_we_need', '')

    # Step 3: Build friendly guidance response
    response_lines = []
    response_lines.append(f"Great question! Let me help you think through this.\n")

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
