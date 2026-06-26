# Machine Downtime Log

A real-time downtime tracking system for manufacturing floors, designed to run on Cisco Secure AI Factory infrastructure.

## Overview

The Machine Downtime Log automatically tracks machine stoppages on a manufacturing floor by monitoring a live event stream and logging each stoppage as a downtime ticket. Each ticket records:
- Machine ID
- Machine type  
- Start time
- End time
- Downtime in minutes
- Automatic classification (reason and severity)
- Optional manual notes

The system provides a live dashboard showing:
- Total downtime minutes today across all machines
- Worst performing machine by downtime today
- Running list of recent events
- Event-to-display latency indicator
- Security/on-prem operation indicator
- LLM connectivity status indicator

## Cisco Secure AI Factory Benefits

Running on a Cisco Secure AI Factory provides:

### 🔒 **On-Prem Security**
- All data processing occurs locally within your secure network
- No data leaves your premises - LLM inference runs on-premises
- Reduced attack surface compared to cloud-based solutions
- Full compliance with data sovereignty requirements

### ⚡ **High-Performance Network**
- Low-latency event processing for real-time monitoring
- Optimized for industrial automation networks
- Reliable connectivity for 24/7 manufacturing operations
- Quality of Service (QoS) prioritization for critical signals

### 📊 **Splunk Visibility**
- Structured JSON logging compatible with Splunk
- Real-time operational metrics for dashboarding
- Easy integration with existing Splunk SIEM
- Searchable downtime trends and root cause analysis

## Features

- **Automatic Event Processing**: Watches live event stream and logs downtime tickets automatically
- **LLM-Powered Classification**: Uses on-prem NVIDIA Nemotron model to classify events by reason and severity
- **Defensive Design**: Graceful fallback if LLM is unreachable - never crashes the event stream
- **Manual Notes**: Operators can add context to automatic classifications
- **Real-Time Dashboard**: Live updates via Server-Sent Events
- **Event Simulator**: Built-in toggleable simulator for testing and development
- **Secure by Design**: Runs as non-root user, uses trusted base image
- **Production Ready**: Health checks, structured logging, Dockerized deployment

## Configuration

All configuration is done via environment variables with sensible defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_PORT` | `8742` | Port to expose the web application |
| `LLM_BASE_URL` | `http://198.18.5.11:8000/v1` | Base URL for the LLM vLLM server |
| `LLM_MODEL` | `/ai/models/NVIDIA/Nemotron-3-120B/` | Model identifier for the LLM |
| `LLM_API_KEY` | `LLM` | API key for LLM authentication |
| `LLM_TIMEOUT_SECONDS` | `15` | Timeout for LLM requests in seconds |
| `DB_PATH` | `/data/downtime.db` | Path to SQLite database file |
| `SIMULATOR_ENABLED` | `true` | Whether to run the built-in event simulator |
| `SIMULATOR_INTERVAL_SECONDS` | `8` | Interval between simulated events (seconds) |

## Deployment

### Prerequisites
- Docker Engine 20.10+
- Docker Compose v2+
- Access to GitHub Container Registry (for published images)
- On-prem vLLM server running NVIDIA Nemotron model (for LLM features)

### Local Development

1. Clone the repository:
   ```bash
   git clone https://github.com/pl247/team-1.git
   cd Machine-Downtime-Log
   ```

2. Create a `.env` file (optional - overrides defaults):
   ```bash
   cp .env.example .env  # if you have an example
   # or create your own:
   echo "APP_PORT=8742" > .env
   echo "LLM_BASE_URL=http://your-llm-server:8000/v1" >> .env
   ```

3. Start the application:
   ```bash
   docker compose up --build
   ```

4. Access the dashboard at: http://localhost:8742

### Production Deployment

1. Pull the published image from GHCR:
   ```bash
   docker pull ghcr.io/pl247/team-1:latest
   ```

2. Run with docker-compose:
   ```bash
   docker compose up -d
   ```

3. Or run directly with Docker:
   ```bash
   docker run -d \
     -p 8742:8742 \
     -v downtime-data:/data \
     -e APP_PORT=8742 \
     -e LLM_BASE_URL=http://your-llm-server:8000/v1 \
     -e LLM_MODEL=/ai/models/NVIDIA/Nemotron-3-120B/ \
     -e LLM_API_KEY=your-api-key \
     -e DB_PATH=/data/downtime.db \
     -e SIMULATOR_ENABLED=false \
     ghcr.io/pl247/team-1:latest
   ```

## API Endpoints

- `GET /` - Serves the HTML dashboard
- `GET /health` - Health check endpoint
- `GET /api/events` - Get recent downtime events (query param: `limit`)
- `GET /api/stats` - Get dashboard statistics
- `POST /api/events/{id}/notes` - Add manual notes to an event

## Project Structure

```
Machine-Downtime-Log/
├── Dockerfile                 # Container image definition
├── docker-compose.yml        # Deployment configuration
├── requirements.txt          # Python dependencies
├── main.py                   # FastAPI application
├── storage.py                # SQLite data layer
├── llm_client.py             # LLM integration with fallback
├── static/
│   └── index.html            # Single-page dashboard
├── .github/
│   └── workflows/
│       └── docker-publish.yml # CI/CD pipeline
├── .gitignore                # Git ignore rules
└── README.md                 # This file
```

## Development

### Running Locally Without Docker

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set environment variables (optional):
   ```bash
   export APP_PORT=8742
   export LLM_BASE_URL=http://198.18.5.11:8000/v1
   # ... etc
   ```

3. Start the application:
   ```bash
   python main.py
   ```

### Testing

The application includes a built-in event simulator that can be toggled via the `SIMULATOR_ENABLED` environment variable. When enabled, it generates realistic machine stoppage events every `SIMULATOR_INTERVAL_SECONDS` seconds (default: 8 seconds).

## Logging

The application outputs structured log messages to stdout suitable for ingestion by Splunk:

```
2026-06-26 13:45:22,123 INFO machine-downtime-log event_type=downtime_detected machine_id=CNC-001 machine_type="CNC Mill" downtime_minutes=45.2 reason_category="Mechanical Failure" severity="High" source=simulator
```

## License

This project is proprietary software for internal use within the Cisco Secure AI Factory ecosystem.

## Support

For issues or questions, please refer to the internal Cisco Secure AI Factory documentation or contact your system administrator.