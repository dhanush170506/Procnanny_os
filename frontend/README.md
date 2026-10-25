# ProcNanny dashboard

This React + Vite frontend fetches read-only process snapshots, monitor status,
and JSONL log records from the FastAPI backend by default. Missing backend data
is not replaced with fabricated samples. Set `VITE_DEMO_MODE=true` in
`.env.local` only when you intentionally want the clearly labeled UI demo.

For complete setup and instructions to run both applications, see the
[ProcNanny project README](../README.md).

## Run the dashboard

Start the backend first from the repository root:

```powershell
python -m uvicorn procnanny.api:app --host 127.0.0.1 --port 8000
```

Then, from this directory:

```powershell
npm install
npm run dev
```

## Build and test

```powershell
npm test
npm run build
```

To preview the production build, run `npm run preview`.
