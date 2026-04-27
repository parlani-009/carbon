# CarBot

An AI-powered car buying assistant that helps users find, compare, and learn about vehicles through natural conversation.

## Tech Stack

- **Frontend**: React 19 + Vite, React Markdown for rendering
- **Backend**: Django 4.2 + Django REST Framework
- **AI**: Anthropic/MiniMax-M2 via structured LLM calls
- **Task Queue**: Celery with Redis broker
- **Database**: PostgreSQL
- **Cache**: Redis

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend   │────▶│   Backend   │────▶│  PostgreSQL │
│   (React)    │◀────│   (Django)  │◀────│             │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                    ┌─────▼─────┐
                    │   Celery   │ (async task processing)
                    │  + Redis  │
                    └───────────┘
```

## Features

### Intelligent Chat Modes

CarBot classifies each user query into one of four modes using an LLM with structured output:

1. **Q&A Mode** — Answer specific questions about car specs, features, or explanations
2. **Comparison Mode** — Compare cars side-by-side with structured tables
3. **Retrieval Mode** — Find and recommend cars based on user needs and preferences
4. **Guidance Mode** — Help confused users by asking targeted follow-up questions

## Algorithms Implemented

### 1. Intent Classification Pipeline

When a user sends a message, the system classifies intent via:

1. **Chat History Assembly** — Fetches all prior messages from the database and builds a context string
2. **Structured LLM Call** — Calls `structured_llm()` with a schema that outputs:
   - `selected_mode`: qna | comparison | retrieval | guidance
   - `reasoning`: Why this mode was chosen
   - `confidence`: 0-1 confidence score
3. **Mode Dispatch** — Celery task is dispatched based on classified mode

### 2. Structured LLM Wrapper (`carbot/minmax.py`)

The `structured_llm()` function provides a robust interface for JSON-constrained LLM calls:

```python
structured_llm(prompt, output_schema, system_prompt=None, execute_tool=None)
```

**Process:**
1. Injects a system prompt requiring ONLY valid JSON matching the schema
2. Sends request to MiniMax-M2 model
3. **Tool call handling** — If the model returns tool_use blocks, executes them via callback
4. **JSON extraction** — Parses response content, handling both text and tool_result blocks
5. **Fallback regex** — If response is plain text with embedded JSON, uses regex to extract

### 3. Celery Task Workers

Four async tasks handle different modes:

#### Q&A Task (`process_qna_task`)
- Assembles chat history context
- Prompts LLM as a "god car salesperson" to answer with expertise
- Stores response as PLAIN_TEXT message

#### Comparison Task (`process_comparison_task`)
1. **Car Extraction** — Uses LLM to extract car names from query (company + model)
2. **Database Lookup** — Queries PostgreSQL for matching cars (case-insensitive partial match)
3. **Validation** — Requires at least 2 cars found, otherwise returns helpful error
4. **Comparison Generation** — Calls LLM with car details to generate:
   - Summary
   - Category-by-category breakdown (Performance, Value, Features, etc.)
   - Winner per category
   - Final verdict
5. Stores as COMPARISON message with markdown rendering

#### Retrieval Task (`process_retrieval_task`)
1. **Filter Extraction** — Uses LLM to extract structured filters from natural language:
   - Price range, seat count, fuel type, horsepower minimum, body type, usage
2. **Vague Query Handling** — If `is_vague=true`, generates 3-4 follow-up questions instead
3. **Database Query** — Builds Django ORM query from extracted filters
4. **LLM Ranking** — If >10 results, fetches 20 and uses LLM to rank top 10 with explanations
5. Stores as AGENT_QUESTION message

#### Guidance Task (`process_guidance_task`)
- Analyzes full chat history to identify what the user has shared
- Determines key missing information
- Asks ONE focused question that will have the biggest impact
- Stores as AGENT_QUESTION with metadata (what_we_know, what_we_need)

### 4. Data Parsing (`api/management/commands/load_cars.py`)

Smart parsing for messy CSV car data:

**Price Parsing:**
- Handles price ranges like "$12,000-$15,000" by averaging
- Strips currency symbols and commas

**Numeric Extraction:**
- Removes all non-numeric characters except decimal and minus
- Handles missing/invalid values gracefully

**Engine Capacity:**
- Extracts first numeric value from strings like "3,982 cc"

### 5. Frontend Polling

The React frontend implements efficient message polling:
- Polls every 2 seconds for new messages when a chat is active
- Smart scroll: only auto-scrolls if user is already at the bottom
- Graceful handling of loading/empty states

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/` | Send message, returns classification + dispatches task |
| GET | `/api/chat/list/` | List all chats for sidebar |
| POST | `/api/chat/make/` | Create new chat |
| GET | `/api/chat/messages/` | Get messages for a chat (polling) |

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Anthropic API key

### Environment Variables

Create `backend/.env`:
```
ANTHROPIC_API_KEY=your_api_key
ANTHROPIC_BASE_URL=https://api.minimax.io
```

### Run with Docker Compose

```bash
docker-compose up --build
```

Services:
- **Frontend**: http://localhost:5173
- **Backend**: http://localhost:8000
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379

### Local Development

**Backend:**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_cars   # load sample car data
python manage.py runserver
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

**Celery (separate terminal):**
```bash
celery -A carbot worker --loglevel=info --pool=solo
```

## Data Model

### Car
- `company`, `name`, `engine`, `engine_capacity` (CC)
- `horsepower` (HP), `total_speed` (km/h), `acceleration` (0-100 km/h seconds)
- `price` ($), `fuel_type`, `seats`, `torque` (Nm)

### Chat / Message
- Chat contains multiple Messages
- Message types: `user_input`, `agent_question`, `comparison`, `plain_text`
- Content stored as JSON for flexibility

## Database

The system ships with ~450 cars loaded from `cars_data.csv` including:
- Ferrari, Rolls Royce, Bentley, Lamborghini
- BMW, Mercedes, Audi, Toyota, Ford
- And more spanning supercars to economy vehicles