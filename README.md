# Star Agents Orchestrator 🤖

AI Agent Orchestration Platform - Python Migration from N8N

## 🎯 Overviewa

This is a Python-based AI agent orchestration platform migrated from N8N to enable:
- ✅ Development assisted by AI (Claude Code, Cursor)
- ✅ Native SSE streaming for better UX
- ✅ Full observability with OpenTelemetry
- ✅ 4-5x faster development iteration
- ✅ 100% functional parity with N8N

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15+ (or use Docker)
- OpenAI API Key

### Local Development Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd star-agents
   ```

2. **Create `.env` file**
   ```bash
   cp .env.example .env
   # Edit .env and add your OPENAI_API_KEY and DATABASE_URL
   ```

3. **Install dependencies with Poetry**
   ```bash
   # Install Poetry if you haven't
   curl -sSL https://install.python-poetry.org | python3 -

   # Install project dependencies
   poetry install
   ```

4. **Run with Docker Compose** (Recommended)
   ```bash
   # Start all services (app + postgres)
   docker-compose up -d

   # View logs
   docker-compose logs -f app

   # Stop services
   docker-compose down
   ```

5. **Or run locally** (if you have PostgreSQL running)
   ```bash
   poetry run uvicorn app.main:app --reload --port 8000
   ```

## 📡 API Endpoints

### Health Check
```bash
# Basic health
curl http://localhost:8000/api/health

# Readiness (includes DB check)
curl http://localhost:8000/api/readiness
```

### Root
```bash
curl http://localhost:8000/
```

## 🏗️ Project Structure

```
star-agents/
├── app/
│   ├── config.py           # Settings & environment variables
│   ├── database.py         # Async DB engine & session
│   ├── main.py            # FastAPI application
│   ├── db/                # SQLAlchemy ORM models
│   ├── models/            # Pydantic schemas
│   ├── routes/            # API endpoints
│   ├── services/          # Business logic
│   ├── repositories/      # Data access layer
│   └── utils/             # Helpers
├── migrations/            # Alembic migrations
├── docker/               # Docker files
├── docs/                 # Documentation
└── pyproject.toml        # Poetry dependencies
```

## 🗄️ Database

The application uses PostgreSQL with the existing schema from N8N.

### Key Tables
- `companies` - Multi-tenant root
- `agents` - Main agent configuration
- `sub_agents` - Agent states/personas
- `steps` - Workflow steps
- `decision_rules` - State transition rules
- `tools` - Available functions
- `customers` - User sessions
- `chat_history` - Conversation messages

## 🛠️ Development

### Code Quality

```bash
# Linting
poetry run ruff check app/

# Format code
poetry run ruff format app/

# Type checking
poetry run mypy app/
```

### Environment Variables

See [.env.example](.env.example) for all available configuration options.

## 📚 Documentation

- [PRD](docs/prd.md) - Product Requirements Document
- [Database Schema](docs/database_schema_documentation.md) - Complete DB documentation
- [N8N Workflow](docs/n8n_workflow.json) - Original N8N workflow

## 🔄 Migration Status

### ✅ Phase 1: Foundation (Current)
- [x] Project structure
- [x] Database models (SQLAlchemy)
- [x] FastAPI setup
- [x] Health check endpoints
- [x] Docker environment

### 🚧 Phase 2: Core Services (Next)
- [ ] Context Builder service
- [ ] Message Handler service
- [ ] Chat History repository
- [ ] Customer repository
- [ ] Chat endpoint (POST /api/chat)

### 📅 Phase 3: Streaming
- [ ] SSE streaming endpoint
- [ ] Real-time message delivery

### 📅 Phase 4: Tool Execution
- [ ] Tool executor service
- [ ] Recursive tool calling
- [ ] Decision rules & transitions

## 🤝 Contributing

This is an internal migration project. See the PRD for full roadmap and specifications.

## 📝 License

[Your License Here]
