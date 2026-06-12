# Machine Downtime Log

A field-services ticket tracker for manufacturing-floor machine stoppages that automatically logs downtime tickets from live machine event streams.

## Features

- **Real-time Monitoring**: Watches live event streams of machine events and automatically logs stoppages as downtime tickets
- **Automatic Ticket Creation**: For each stoppage records machine ID, type, start time, end time, and computed downtime in minutes
- **Live Dashboard**: Shows total downtime minutes today across all machines and highlights the single worst machine by most downtime minutes today
- **Manual Notes**: Ability to add manual notes to tickets (currently auto-generated, UI for manual notes can be extended)
- **On-Prem LLM Integration**: Uses locally hosted NVIDIA Nemotron model via vLLM (OpenAI-compatible API) to classify downtime reason and assign severity (Low/Medium/High/Critical)
- **Low Latency UI**: Events react instantly with on-screen indicator showing event-to-display latency in milliseconds
- **Built-in Event Simulator**: Toggleable simulator for testing and development
- **Zero-Setup Storage**: Uses SQLite with persistence via Docker volume
- **Port Conflict Detection**: Checks if configured port is available on startup
- **Secure & On-Prem**: All processing happens locally with no cloud round-trip for event processing or LLM classification

## Architecture

- **Backend**: Python FastAPI serving REST API and WebSocket/Server-Sent Events for real-time updates
- **Frontend**: Single-page HTML/JavaScript served by FastAPI (no separate build step)
- **Database**: SQLite for zero-setup storage
- **Event Streaming**: Server-Sent Events for real-time UI updates
- **LLM Service**: External locally hosted vLLM server with OpenAI-compatible API
- **Containerization**: Dockerized with slim Python base image

## Environment Variables

All configuration is done via environment variables with sensible defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8742` | Port for the application to listen on |
| `LLM_BASE_URL` | `http://198.18.5.11:8000/v1` | Base URL for the LLM API (vLLM server) |
| `LLM_MODEL` | `/ai/models/NVIDIA/Nemotron-3-120B/` | Model identifier for the LLM |
| `LLM_API_KEY` | `LLM` | API key for the LLM service |
| `LLM_TIMEOUT_SECONDS` | `15` | Timeout for LLM requests in seconds |
| `DB_PATH` | `/data/downtime.db` | Path to SQLite database file |
| `SIMULATOR_ENABLED` | `true` | Enable/disable the built-in event simulator |
| `SIMULATOR_INTERVAL_SECONDS` | `8` | Interval between simulated events in seconds |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Local LLM Integration

The application contacts an external locally hosted LLM (NVIDIA Nemotron via vLLM) for each downtime event:
- Sends the machine event description
- Requests strict JSON response with:
  - `reason_category`: One of ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
  - `severity`: One of ["Low", "Medium", "High", "Critical"]
- Implements defensive parsing with fallback to `Unknown`/`Medium` if LLM is unreachable or returns invalid output
- LLM failures never crash the event stream processing

## Running with Docker

### Prerequisites
- Docker and Docker Compose installed
- Access to a locally hosted LLM service (vLLM with Nemotron model) at the specified LLM_BASE_URL

### Steps
1. Clone this repository
2. Create a `.env` file in the project root (optional) to override defaults:
   ```bash
   APP_PORT=8742
   LLM_BASE_URL=http://198.18.5.11:8000/v1
   LLM_MODEL=/ai/models/NVIDIA/Nemotron-3-120B/
   LLM_API_KEY=your-api-key-here
   LLM_TIMEOUT_SECONDS=15
   DB_PATH=/data/downtime.db
   SIMULATOR_ENABLED=true
   SIMULATOR_INTERVAL_SECONDS=8
   LOG_LEVEL=INFO
   ```
3. Start the application:
   ```bash
   docker compose up
   ```
4. Access the dashboard at `http://localhost:8742` (or your configured APP_PORT)

### Notes
- The first run will create the SQLite database in the `./data` directory (mounted volume)
- The application checks if APP_PORT is available on startup and exits with error if not
- The built-in event simulator runs by default (set SIMULATOR_ENABLED=false to disable)

## GitHub Actions CI/CD

This repository includes a GitHub Actions workflow that:
- Builds the Docker image on pushes to main
- Publishes the image to GitHub Container Registry (ghcr.io/pl247/team-1)
- Uses the GitHub PAT stored in repository secrets

## Directory Structure

```
machine-downtime-log/
├── main.py             # FastAPI application
├── Dockerfile          # Container image definition
├── docker-compose.yml  # Docker Compose configuration
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── .gitignore          # Git ignore rules
└── data/               # SQLite database volume (created on first run)
```

## Development

To run locally without Docker:
```bash
pip install -r requirements.txt
python main.py
```

Then visit `http://localhost:8742`

## License

MIT