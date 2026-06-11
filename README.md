# Machine Downtime Log

A real-time field services ticket tracker for manufacturing floor machine stoppages.

## Overview

The Machine Downtime Log application automatically detects machine stoppages from a live event stream, logs them as downtime tickets, and provides a live dashboard showing:
- Total downtime minutes today across all machines
- The single worst-performing machine (most downtime minutes today) with visual highlighting
- Real-time latency measurement from event to display
- Automatic classification of downtime reasons and severity using an on-premises LLM

## Key Features

✅ **Automatic Ticket Logging** - No manual entry required  
✅ **Real-Time Dashboard** - Updates via Server-Sent Events (SSE)  
✅ **On-Premises LLM Classification** - Uses local Llama 70B model via vLLM (no cloud round-trip)  
✅ **Low Latency** - Event-to-display latency shown in milliseconds  
✅ **Built-in Simulator** - Toggleable event stream for demonstration  
✅ **Persistent Storage** - SQLite database with volume mounting  
✅ **Zero Configuration** - Works out of the box with sensible defaults  
✅ **Dockerized** - Runs on Ubuntu with docker-compose  

## Architecture

- **Backend**: Python/FastAPI with SQLite
- **Frontend**: Single-page HTML/JavaScript (served by FastAPI)
- **Real-time Updates**: Server-Sent Events (SSE)
- **LLM Integration**: OpenAI-compatible API to local vLLM server
- **Event Simulation**: Built-in toggleable simulator
- **Containerization**: Docker with docker-compose

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8742` | Port for the web application |
| `LLM_BASE_URL` | `http://198.18.5.11:8000/v1` | Base URL for the local LLM API |
| `LLM_MODEL` | `llama-70b` | Model name to use with the LLM |
| `LLM_TIMEOUT_SECONDS` | `15` | Timeout for LLM requests in seconds |
| `DB_PATH` | `/data/downtime.db` | Path to SQLite database file |
| `SIMULATOR_ENABLED` | `true` | Set to `false` to disable the event simulator |
| `SIMULATOR_INTERVAL_SECONDS` | `8` | Interval between simulated events (seconds) |

## Local LLM Integration

The application sends machine event descriptions to a locally hosted LLM (Llama 70B via vLLM) running at `http://198.18.5.11:8000/v1`. The LLM is prompted to return a JSON object with:
- `reason_category`: One of ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
- `severity`: One of ["Low", "Medium", "High", "Critical"]

If the LLM is unreachable or returns invalid output, the application falls back to:
- `reason_category`: "Unclassified"
- `severity`: "Medium"

This ensures the application continues to run even if the LLM is temporarily unavailable.

## Running with Docker

### Prerequisites
- Docker and docker-compose installed
- Access to the local LLM at `http://198.18.5.11:8000/v1` (or adjust `LLM_BASE_URL`)

### Steps
1. Clone this repository
2. Run: `docker compose up -d`
3. Open your browser to `http://localhost:8742` (or the port you configured)

### Persistent Data
The SQLite database is stored in the `./data` directory on the host, mounted to `/data` in the container. This ensures downtime history survives container restarts.

## GitHub Actions Workflow

This repository includes a GitHub Actions workflow (`.github/workflows/docker-publish.yml`) that:
1. Builds the Docker image on pushes to the main branch
2. Publishes the image to GitHub Container Registry (ghcr.io/pl247/team-1)

To use the published image:
```bash
docker pull ghcr.io/pl247/team-1:latest
docker run -p 8742:8742 -v ./data:/data ghcr.io/pl247/team-1:latest
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

--- 
*Note: This application is designed for on-premises use in manufacturing environments. All data remains local, and no factory data leaves the premises.*