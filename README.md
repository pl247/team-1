# Machine Downtime Log

A real-time application for tracking machine stoppages on manufacturing floors. Automatically logs downtime events with machine ID, type, start/end times, duration, and AI-generated root cause analysis.

## Overview

The Machine Downtime Log watches live machine event streams and automatically creates downtime tickets for each stoppage. Each ticket includes:
- Machine ID and type
- Start and end timestamps
- Calculated downtime in minutes
- Free-text description
- AI-classified reason category (Mechanical Failure, Operator Error, Material Shortage, Maintenance, Power Loss, Unknown)
- AI-assessed severity (Low, Medium, High, Critical)

Users can add manual notes to events for additional context.

## Cisco Secure AI Factory Benefits

This application is designed to run in a Cisco Secure AI Factory environment, providing:
- **On-Prem Processing**: All data and AI inference stays within your secure network
- **Security**: No external API calls for core processing; sensitive operational data never leaves premises
- **High-Performance Network**: Optimized for low-latency event processing in industrial settings
- **Splunk Visibility**: Structured JSON logging to stdout for seamless integration with Splunk monitoring and analytics

## Features

- Real-time event processing with automatic downtime calculation
- AI-powered root cause analysis using locally hosted NVIDIA Nemotron model
- Live dashboard showing:
  - Total downtime minutes today across all machines
  - Worst-performing machine by downtime
  - Running list of recent events
  - Event-to-display latency indicator
  - On-prem secure operation indicator
  - LLM connectivity status indicator
- Manual note attachment to events
- Built-in event simulator (toggleable via environment variable)
- Structured logging compatible with Splunk
- Dockerized for easy deployment
- Automatic port conflict detection on startup

## Environment Variables

All configuration is done via environment variables with sensible defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8742` | Port for the web interface and API |
| `LLM_BASE_URL` | `http://198.18.5.11:8000/v1` | Base URL for the vLLM server |
| `LLM_MODEL` | `/ai/models/NVIDIA/Nemotron-3-120B/` | Model identifier for the NVIDIA Nemotron model |
| `LLM_API_KEY` | `LLM` | API key for authenticating with the LLM service |
| `LLM_TIMEOUT_SECONDS` | `15` | Timeout in seconds for LLM requests |
| `DB_PATH` | `/data/downtime.db` | Path to SQLite database file |
| `SIMULATOR_ENABLED` | `true` | Enable/disable the built-in event simulator |
| `SIMULATOR_INTERVAL_SECONDS` | `8` | Interval between simulated events (seconds) |

## Deployment

### Running on Ubuntu

1. **Clone the repository**:
   ```bash
   git clone https://github.com/pl247/team-1.git
   cd team-1
   ```

2. **Set environment variables** (create a `.env` file or export):
   ```bash
   export APP_PORT=8742
   export LLM_BASE_URL=http://198.18.5.11:8000/v1
   export LLM_MODEL=/ai/models/NVIDIA/Nemotron-3-120B/
   export LLM_API_KEY=LLM
   export LLM_TIMEOUT_SECONDS=15
   export DB_PATH=/data/downtime.db
   export SIMULATOR_ENABLED=true
   export SIMULATOR_INTERVAL_SECONDS=8
   ```

3. **Build and run with Docker Compose**:
   ```bash
   docker compose up -d
   ```

4. **Access the dashboard**:
   Open your browser to `http://localhost:8742`

### Pulling from GHCR

To run the pre-built image from GitHub Container Registry:

```bash
docker pull ghcr.io/pl247/team-1:latest

# Run with required environment variables
docker run -d \
  -p 8742:8742 \
  -e APP_PORT=8742 \
  -e LLM_BASE_URL=http://198.18.5.11:8000/v1 \
  -e LLM_MODEL=/ai/models/NVIDIA/Nemotron-3-120B/ \
  -e LLM_API_KEY=LLM \
  -e LLM_TIMEOUT_SECONDS=15 \
  -e DB_PATH=/data/downtime.db \
  -e SIMULATOR_ENABLED=true \
  -e SIMULATOR_INTERVAL_SECONDS=8 \
  -v $(pwd)/data:/data \
  ghcr.io/pl247/team-1:latest
```

## Architecture

- **Backend**: Python/FastAPI serving REST API and static frontend
- **Frontend**: Single HTML page with JavaScript served directly by backend
- **Storage**: SQLite database persisted to `/data` volume
- **Real-time Updates**: Server-Sent Events (SSE) for live dashboard updates
- **AI Integration**: OpenAI-compatible API call to local vLLM-hosted Nemotron model
- **Containerization**: Docker image based on slim Python 3.12
- **Orchestration**: Docker Compose for local development, GitHub Actions for CI/CD

## Local Development

For development without Docker:

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set environment variables as shown above

3. Run the application:
   ```bash
   python main.py
   ```

## Logging

The application outputs structured JSON logs to stdout suitable for Splunk ingestion:
```json
{
  "timestamp": "2026-06-26T12:00:00Z",
  "level": "INFO",
  "message": "Event started: M001 - Unexpected vibration detected",
  "component": "event_processor",
  "event_id": 123
}
```

## License

This project is proprietary software designed for Cisco Secure AI Factory deployments.