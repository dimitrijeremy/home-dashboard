# home-dashboard

Home Dashboard is a Dockerized CCTV dashboard using Flask (backend), MediaMTX (media server) and React (Vite) frontend with HLS.js.

**Architecture**

ASCII diagram:

backend (Flask) <---> mediamtx (RTSP/HLS) <---> frontend (React + HLS.js)

Services:
- backend: Provides REST API `/api/cameras` that returns camera list with HLS URLs
- mediamtx: Converts RTSP camera streams to HLS
- frontend: React app using `hls.js` to play camera streams

## Project structure

home-dashboard/
  backend/
  frontend/
  mediamtx.yml
  docker-compose.yml
  .gitignore
  README.md

## Run locally

1. Configure your RTSP source in `mediamtx.yml` under `paths.frontdoor.source`.

2. Build and start services:

```bash
docker compose up --build
```

3. Open the frontend at: `http://localhost:5173`

## Configure RTSP source

Edit `mediamtx.yml` and set the `source` field for `frontdoor` to your camera RTSP URL, e.g. `rtsp://user:pass@192.168.1.100:554/stream`.

## GitHub

Initialize repository and push:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<username>/home-dashboard.git
git push -u origin main
```

## Future Roadmap

- Add ONVIF discovery
- Add authentication and user management
- Integrate AI for motion detection and object recognition
- Multi-camera layout presets and recording

