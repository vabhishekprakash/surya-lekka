# Surya Lekka

## Problem

## How it works

## Architecture

## Results

## Limits

## Run it

You need Python 3.12. From the repo root:

```
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

To try the web app on your own machine, with no AWS account:

```
python -m src.api.local_server
```

Then open http://127.0.0.1:8000/. The server keeps everything in memory, and a stub stands in for the model, so every uploaded quote comes back with the same made-up reading. The page loads pdf.js from cdnjs, so turning a PDF into page images needs an internet connection; photos don't. Options: `--port`, `--stub slow` (the stub waits 75 seconds per call, long enough to show the slow-reading message), `--stub fail-once` (the first reading of each upload fails, so you can try the retry button) and `--daily-cap 0` (shows the daily limit message).

To point the web app at a deployed API, set `API_BASE` in `web/config.js` to the stack's `ApiUrl` output.
