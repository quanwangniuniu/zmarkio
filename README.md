# Marketing Simplified

> **The best media buyer Jira platform in the world**
> Marketing Simplified is a campaign management platform tailored for media buying teams. It streamlines the process of creating, tracking, and optimizing advertising campaigns, while providing collaboration tools, performance analytics, budget control, and professional API access.

---

## ✨ Features

- **Campaign Management**
  Plan and execute campaign workflows, including lifecycle states, ownership, and approvals across teams.
- **Task Management**
  Comprehensive task tracking with assignments, status updates, and workflow automation.
- **Team Collaboration**
  Assign roles and permissions for seamless teamwork with real-time chat and notifications.
- **Real-time Chat**
  WebSocket-based messaging system for team communication and collaboration.
- **Calendar Integration**
  Google Calendar-style event management with recurring events, reminders, and sharing capabilities.
- **Decision Tracking**
  Track and document important decisions with approval workflows.
- **Workflow Automation**
  Visual workflow builder with automation canvas for process optimization.
- **Performance Tracking**
  Monitor campaign outcomes with reporting on delivery and efficiency metrics (for example impressions, clicks, conversions, and spend).
- **Budget Tracking & Alerts**
  Track budget usage and receive alerts when limits are exceeded with approval workflows.
- **Multi-Platform Integration**
  Integrate with Facebook Meta, Google Ads, TikTok, Klaviyo, Mailchimp, and other advertising platforms.
- **Asset Management**
  Upload, organize, and manage creative assets with virus scanning and version control.
- **Spreadsheet Functionality**
  Advanced spreadsheet features with formula engine and data manipulation.
- **OpenAPI-based API Access**
  Integrate with third-party systems via a fully documented REST API with OpenAPI specifications.
- **Background Jobs**
  Run asynchronous and scheduled workloads with Celery workers and Celery Beat for long-running or periodic tasks.
- **Event Streaming**
  Use Kafka-based event pipelines for decoupled, event-driven workflows across services.
- **Observability**
  Monitor and troubleshoot the system with Prometheus metrics, Grafana dashboards, Loki logs, and Jaeger distributed tracing.

* **Miro Collaboration**
  Create, edit, and manage visual boards for brainstorming, planning, and workflow collaboration.
* **Spreadsheet Workspace**
  Manage structured data with spreadsheet-style editing, formulas, and analysis workflows.
* **AI Agent Workflows**
  Run AI-assisted task workflows for data processing, decision support, and execution guidance.
* **Meetings & Action Items**
  Capture meeting outcomes and track action items through follow-up workflows.
* **Integrations Hub**
  Connect with external platforms (Notion, Slack, Google, Zoom, Linear, Meta/TikTok, etc.) for cross-system collaboration.

---

## 🛠 Tech Stack

**Frontend**

- Next.js 14
- React 18
- TypeScript
- Tailwind CSS
- Radix UI (component library)
- Zustand (state management)
- Axios (API requests)
- Pino (structured logging)
- OpenTelemetry (distributed tracing)
- KafkaJS (event streaming)
- Storybook (component development)

**Backend**

* Django 4.2
* Django REST Framework
* Django Channels (WebSocket support)
* Celery (background tasks)
* PostgreSQL
* Redis (caching & message broker)
* OpenTelemetry (distributed tracing)
* kafka-python (event streaming)
* python-json-logger / json-log-formatter (structured logging)

**Infrastructure (Development)**

- Docker & Docker Compose
- Nginx reverse proxy
- Redis
- ClamAV
- Kafka + Kafka UI
- Prometheus + Grafana + Loki + Jaeger
- InfluxDB (for K6 metrics)

**Testing**

- Jest (frontend unit testing)
- pytest (backend testing)
- Playwright / Cypress (frontend e2e)
- K6 (load testing)

---

## 📂 Repository Structure

```text
.
├── backend/                    # Django backend source code
│   ├── backend/                # Django project settings/urls/asgi
│   ├── campaign/               # Campaign management app
│   ├── task/                   # Task management app
│   ├── chat/                   # Real-time chat app
│   ├── calendars/              # Calendar management app
│   ├── meetings/               # Meeting workflows app
│   ├── decision/               # Decision tracking app
│   ├── automationWorkflow/     # Workflow automation app
│   ├── agent/                  # Agent workflows app
│   ├── spreadsheet/            # Spreadsheet functionality app
│   ├── miro/                   # Miro integration app
│   ├── slack_integration/      # Slack integration app
│   ├── notion_editor/          # Notion editor integration app
│   ├── linear_integration/     # Linear integration app
│   ├── zoom_integration/       # Zoom integration app
│   ├── google_calendar_integration/     # Google Calendar integration app
│   ├── google_docs_integration/         # Google Docs integration app
│   ├── ...                     # Other Django apps
│   ├── manage.py               # Django management entrypoint
│   └── requirements.txt        # Backend Python dependencies
├── frontend/                   # Next.js frontend source code
│   ├── src/
│   │   ├── app/                # Next.js app router pages
│   │   ├── components/         # React components
│   │   ├── lib/                # Utilities and API clients
│   │   └── ...                 # Other frontend code
│   └── ...                     # Frontend configuration
├── nginx/                      # Nginx configuration files
├── devops/                     # DevOps and infrastructure configs
│   ├── prometheus/             # Prometheus configuration
│   ├── grafana/                # Grafana dashboards
│   ├── elk/                    # ELK Stack configuration
│   ├── sonarqube/              # SonarQube configuration (not active by default in dev compose)
│   └── ...                     # Other DevOps tools
├── k6/                         # K6 load testing scripts and configs
│   ├── scripts/                # Test scenarios and flows
│   └── ...                     # K6 configuration
├── openapi/openapi_spec/       # OpenAPI specification files
├── docs/                       # Additional documentation
├── docker-compose.dev.yml      # Docker Compose for development
├── docker-compose.yml          # Additional compose setup (non-dev/CI profile usage)
├── env.example                 # Root environment variable template
├── DOCKER_README.md            # Detailed Docker deployment guide
├── CICD_README.md              # CI/CD pipeline documentation
└── ...                         # Other project files
```

---

## 🧩 Core Modules

- **campaign**: Manages campaign lifecycle, execution status, and cross-team coordination.
- **task**: Handles task creation, assignment, status transitions, and workflow-linked execution.
- **chat**: Provides real-time team communication and collaboration messaging flows.
- **meetings**: Supports meeting lifecycle management, summaries, and action-item tracking.
- **miro**: Enables visual board collaboration and Miro-related workflow artifacts.
- **spreadsheet**: Provides spreadsheet-style data operations, structured analysis, and tabular workflows.
- **agent**: Powers AI-assisted workflow orchestration, decision support, and execution guidance.
- **automationWorkflow**: Defines and executes automation flows across business modules.
- **decision**: Tracks decision records, approval states, and related governance flows.
- **integrations** (`notion_editor`, `slack_integration`, `linear_integration`, `zoom_integration`, `google_calendar_integration`, `google_docs_integration`): Connects Marketing Simplified with external systems for synchronized workflows.

---

## 🚀 Quick Start (Docker Development)

For additional Docker setup and deployment details, see [DOCKER_README.md](DOCKER_README.md).

### Prerequisites

- Docker Desktop installed and running
- Docker Compose (included in Docker Desktop)
- Local PostgreSQL running on host machine (Docker dev backend connects through `host.docker.internal`)
- Git

### 1. Clone and setup

```bash
git clone <your-repo-url>
cd marketing-simplified

# Copy environment file
cp env.example .env

```

### 2. Configure environment variables

- The only template file in this repository is `env.example` at repo root.
- There is no `.env.example`, `backend/.env.example`, or `frontend/.env.example`.
- Update `.env` with valid local values before startup, especially database credentials and integration keys you need.

Minimum local DB-related values:

```env
DB_HOST=host.docker.internal
POSTGRES_DB=Marketing Simplified_db
POSTGRES_USER=Marketing Simplified_user
POSTGRES_PASSWORD=<your-password>
POSTGRES_PORT=5432
```

> Note: For the current `docker-compose.dev.yml` workflow, `DB_HOST` is overridden to `host.docker.internal` by the backend and Celery service definitions. This means the dev containers connect to PostgreSQL running on your host machine, even if `env.example` contains `DB_HOST=db`.

### 3. Start development environment

```bash
docker compose -f docker-compose.dev.yml --env-file .env up --build -d
```

### 4. Useful day-to-day commands

```bash
# List running services
docker compose -f docker-compose.dev.yml --env-file .env ps

# Follow logs
docker compose -f docker-compose.dev.yml --env-file .env logs -f

# Stop services
docker compose -f docker-compose.dev.yml --env-file .env down

# Rebuild and restart
docker compose -f docker-compose.dev.yml --env-file .env up --build -d
```

### 5. Access the app

**Core Services**

- App (via Nginx): http://localhost/
- Frontend (direct): http://localhost:3000
- Backend API (direct): http://localhost:8000

**Infrastructure Services**

- Redis: localhost:6379
- ClamAV: localhost:3310
- PostgreSQL (host machine, not a dev compose service): localhost:5432

**Monitoring & Observability (active in dev compose)**

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001
- Jaeger UI: http://localhost:16686
- Loki: http://localhost:3100
- InfluxDB (K6 metrics): http://localhost:8086

**Development Tools**

- Kafka UI: http://localhost:8081
- Kafka Metrics Exporter: http://localhost:9308/metrics

**Kafka Access**

- Internal (containers): `kafka:9092`
- External (host): `localhost:29092`

> Note:
> SonarQube, Elasticsearch, and Kibana are not active default services in `docker-compose.dev.yml`.
> For optional setup details, see [DOCKER_README.md](DOCKER_README.md) and [ELK Setup Guide](devops/elk/kibana/ELK_SETUP.md).

## Release SBOMs

Each tagged release (`v*`) publishes CycloneDX SBOM files as GitHub Release assets:

- `Marketing Simplified-backend-python-<tag>.cdx.json`
- `Marketing Simplified-frontend-npm-<tag>.cdx.json`

The same files are also available as workflow artifacts on the release tag run.

## 📊 Monitoring & Observability

Marketing Simplified includes monitoring and observability tools for local development and troubleshooting:

### Metrics Collection

- **Prometheus**: Collects metrics from backend and frontend services
  - Access: http://localhost:9090
  - Metrics endpoints: `/metrics` on backend and `/api/metrics` on frontend

### Visualization

- **Grafana**: Visualize metrics and create dashboards
  - Access: http://localhost:3001
  - Pre-configured dashboards for application metrics
  - K6 load test dashboard for performance monitoring

### Distributed Tracing

- **Jaeger**: End-to-end request tracing across services
  - Access: http://localhost:16686
  - Traces requests through Nginx → Frontend → Backend → Database
  - OpenTelemetry integration for automatic instrumentation

### Logging

**Application Logs (default in dev compose)**

- Structured JSON logging is enabled for Django (`python-json-logger`) and Next.js (`Pino`).
- Use Docker logs for day-to-day debugging:

  - `docker compose -f docker-compose.dev.yml --env-file .env logs -f backend`
  - `docker compose -f docker-compose.dev.yml --env-file .env logs -f frontend`
- **ELK Stack (optional setup)**

  - Elasticsearch, Filebeat, and Kibana are optional and are not active default services in `docker-compose.dev.yml`.
  - If centralized log indexing/search is needed, follow the setup guide:
    - [ELK Setup Guide](devops/elk/kibana/ELK_SETUP.md)

### Metrics Storage

- **InfluxDB**: Time-series database for K6 load test metrics
  - Access: http://localhost:8086
  - Stores performance metrics from load tests
  - Integrated with Grafana for visualization

---

## 🧩 Development Services (Current `docker-compose.dev.yml`)

Service list below is based on `docker-compose.dev.yml`.
Most services are part of the default development stack. Profile-gated services, such as `k6`, only run when their profile is enabled.

- `clamav`
- `redis`
- `backend`
- `frontend`
- `prometheus`
- `jaeger`
- `kafka`
- `topic-init`
- `celery-worker`
- `grafana`
- `influxdb`
- `k6` — Load testing runner, only started with `--profile k6`
- `kafka-exporter`
- `kcat`
- `nginx`
- `celery-beat`
- `kafka-ui`
- `loki`

Common access points:

- App via Nginx: http://localhost/
- Frontend direct: http://localhost:3000
- Backend API direct: http://localhost:8000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001
- Jaeger UI: http://localhost:16686
- Kafka UI: http://localhost:8081
- InfluxDB: http://localhost:8086

Notes:

- SonarQube, Elasticsearch, and Kibana are not active default services in `docker-compose.dev.yml`.
- In dev compose, PostgreSQL is not defined as a containerized service; backend uses host PostgreSQL via `host.docker.internal`.

---

## 🧪 Testing

### Unit & Integration Tests

**Backend (Django + pytest)**

```bash
# Run all backend tests
docker compose -f docker-compose.dev.yml --env-file .env exec backend pytest

# Run with coverage
docker compose -f docker-compose.dev.yml --env-file .env exec backend pytest --cov

# Run specific test file
docker compose -f docker-compose.dev.yml --env-file .env exec backend pytest path/to/test_file.py
```

**Frontend (Next.js + Jest)**

```bash
# Run all frontend tests
docker compose -f docker-compose.dev.yml --env-file .env exec frontend npm run test

# Run tests in watch mode
docker compose -f docker-compose.dev.yml --env-file .env exec frontend npm run test:watch

# Run tests with coverage
docker compose -f docker-compose.dev.yml --env-file .env exec frontend npm run test:coverage

# Run tests in CI mode
docker compose -f docker-compose.dev.yml --env-file .env exec frontend npm run test:ci
```

### Component Testing

- **Storybook**: Component development and testing

  ```bash
  docker compose -f docker-compose.dev.yml --env-file .env exec frontend npm run storybook
  ```

  Access at: http://localhost:6006

### Load Testing

- **K6**: Performance and load testing with InfluxDB metrics storage

  - Smoke test (1 VU, 30 seconds): `python k6/run_smoke_test.py`
  - Load test (10→50 VUs): `python k6/run_load_test.py`
  - Stress test (50→200 VUs): `python k6/run_stress_test.py`
  - Spike test (0→100 VUs): `python k6/run_spike_test.py`

  * Compose profile example: docker compose -f docker-compose.dev.yml --env-file .env --profile k6 run --rm k6 run /scripts/scenarios/smoke-test.js

  See [K6 Load Testing Guide](k6/README.md) for detailed documentation.

### CI/CD Testing

All tests run automatically in GitHub Actions CI/CD pipeline. See [CICD_README.md](CICD_README.md) for details.

---

## 🔧 Additional Services

### Event Streaming

- **Kafka**: Event streaming and messaging system
  - Kafka UI: http://localhost:8081 (Web-based cluster management)
  - Internal broker: `kafka:9092` (from containers)
  - External broker: `localhost:29092` (from host)
  - Metrics: http://localhost:9308/metrics
  - KRaft mode (no Zookeeper dependency)
  - Pre-defined topic management via topic-init container

### Background Processing

- **Celery**: Asynchronous task processing
  - Workers process background jobs (file scanning, report generation, etc.)
  - Redis as message broker
  - Integrated with Django for long-running tasks

### Code Quality

SonarQube is optional and not active by default in **docker-compose.dev.yml**.

### File Security

- **ClamAV**: Virus scanning for uploaded files
  - Port: localhost:3310
  - Automatic scanning of all file uploads
  - Integrated with asset management system

---

## 📄 API Documentation

API specifications are located in `openapi/openapi_spec/` and are served through the **API Docs** page when the application is running.

---

## 🤖 Using Claude Code

This repo ships a shared Claude Code setup so every developer gets the same context and guardrails:

- **[CLAUDE.md](CLAUDE.md)** loads automatically at session start (project overview, commands, gotchas). Topic rules live in `.claude/rules/`.
- Slash commands: `/review` (review a diff or PR against our conventions), `/fix-issue <issue|JIRA-KEY>` (issue → review-ready change).
- Sub-agents: `code-reviewer`, `security-auditor`.
- A developer's job ends at a reviewed PR into `prod-preview`. The `prod-preview` → `main` promotion is the release owner's — `.claude/skills/deploy/` documents that flow for reference only; Claude won't open or drive that PR.
- MCP servers (`.mcp.json`): `github`, `linear`, `atlassian` (Jira), and a read-only `postgres` for your local DB. Run `/mcp` to authenticate each one — see the MCP section of [CLAUDE.md](CLAUDE.md).
- **Claude cannot stage, commit, or push** (`.claude/settings.json` deny-list) and a `PreToolUse` hook blocks dangerous shell commands — you run all git operations yourself.
- Personal, machine-specific notes go in `CLAUDE.local.md` (gitignored — see `CLAUDE.local.md.example`).

---

## 📚 Documentation

For detailed information on specific topics, please refer to the following documentation:

- **[DOCKER_README.md](DOCKER_README.md)**: Comprehensive Docker deployment guide

  - Development vs Production setup
  - Service configuration
  - Troubleshooting guide
  - Common commands
- **[CICD_README.md](CICD_README.md)**: CI/CD pipeline documentation

  - GitHub Actions workflow
  - Testing in CI/CD
  - Best practices for developers
  - Adding new models and migrations
- **[K6 Load Testing Guide](k6/README.md)**: Performance testing documentation

  - Test scenarios (smoke, load, stress, spike)
  - InfluxDB integration
  - Grafana dashboards
  - Performance thresholds
- **ELK Stack Setup**: [devops/elk/kibana/ELK_SETUP.md](devops/elk/kibana/ELK_SETUP.md)

  - Centralized logging setup
  - Kibana dashboard configuration
  - Log retention policies
- **API Specifications**: Located in `openapi/openapi_spec/`

  - OpenAPI 3.0 specifications
  - Available through API Docs page when application is running

---

## 📜 License

This project is licensed under the **LGPL-2.1** license. See the [LICENSE](LICENSE) file for details.
