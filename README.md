# potato-fe — Python/PyQt5 Visual Tool

Real-time industrial potato processing line monitoring GUI built with Python and PyQt5.

## Docker Setup (python-pyqt5)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

### Build & Run

```bash
docker compose up --build
```

### Access the GUI

The PyQt5 application runs inside the container with a virtual display. You can access it in two ways:

| Method | URL / Address | Notes |
|--------|--------------|-------|
| **Web browser (noVNC)** | http://localhost:6080 | No install needed, just open in browser |
| **VNC client** | `localhost:5900` | Use any VNC client (e.g. RealVNC, TigerVNC) |

### Stop

```bash
docker compose down
```

### Live Data Feed

The `feed.json` file is mounted as a volume into the container. To feed live telemetry data:

1. Compile the data generator:
   ```bash
   gcc -o mkjson mkjson.c
   ```
2. Run it on the **host** (outside Docker):
   ```bash
   ./mkjson --stop-after 100
   ```
3. The application inside the container reads `feed.json` every second and updates the UI automatically.

### Architecture

```
Host                          Docker Container
┌─────────────┐               ┌──────────────────────────┐
│             │               │  Xvfb (virtual display)  │
│  feed.json ◄──── volume ────►  main.py (PyQt5 app)    │
│             │               │  x11vnc (:5900)          │
│  Browser   ◄──── :6080 ────►  noVNC  (:6080)          │
│  VNC client◄──── :5900 ────►                           │
└─────────────┘               └──────────────────────────┘
```

### Ports

| Port | Service |
|------|---------|
| 6080 | noVNC (web browser access) |
| 5900 | VNC protocol |

### Troubleshooting

- **Black screen in browser**: Wait a few seconds for the application to start, then refresh.
- **Cannot connect**: Make sure Docker Desktop is running and ports 5900/6080 are not used by another application.
- **No data in the app**: Click the "Start" button in the UI and make sure `feed.json` contains valid telemetry data.
