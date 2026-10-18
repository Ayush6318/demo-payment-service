# demo-payment-service

A small payment service with a monitoring stack, used as a real system for the
Incident Response Agent to investigate. It produces real logs (Loki), real metrics
(Prometheus) and real commits (GitHub), and it has a fault injector.

## Contents
- `app/` payment service (FastAPI + PostgreSQL, JSON logs, Prometheus metrics)
- `monitoring/` Prometheus (+ alert rules), Loki, Promtail, Grafana configs
- `loadgen/load.py` steady traffic generator
- `scripts/inject_fault.py` makes a real git commit that breaks the service, then redeploys

## Quick start (Windows PowerShell, Docker Desktop must be running)
```powershell
cd demo-payment-service
docker compose up -d --build
docker compose ps

Invoke-RestMethod http://localhost:8001/health
```
Open: Grafana http://localhost:3000 | Prometheus http://localhost:9091 | Loki http://localhost:3101/ready

## Put it on GitHub (needed so the code agent can read real commits)
```powershell
git init -b main
git add .
git commit -m "Initial payment-service with monitoring stack"
# create an empty PRIVATE repo named demo-payment-service on github.com, then:
git remote add origin https://github.com/<your-username>/demo-payment-service.git
git push -u origin main

$env:GIT_SHA = (git rev-parse --short HEAD); docker compose up -d --build payment-service
```

## Generate traffic
```powershell
pip install -r loadgen/requirements.txt
python loadgen/load.py
```
Healthy baseline: errors=0, avg_latency about 0.3s.

## Break it on purpose
```powershell
python scripts/inject_fault.py list
python scripts/inject_fault.py inject db_pool       # wait about 2 minutes
python scripts/inject_fault.py revert db_pool
```
| Fault | What happens |
|---|---|
| db_pool | connection pool exhausted, HTTP 500s, alerts HighErrorRate and DbPoolSaturated |
| bad_config | container crash-loops (cannot resolve host db-old), alert ServiceDown |
| memory_leak | memory grows about 1 MB/s, alert HighMemory, then the container is OOM-killed (256 MB limit) |

Always revert one fault before injecting the next. inject_fault.py needs a clean git
working tree, and `git` and `docker` on your PATH.

## Names the agent relies on
- Loki labels: `service="payment-service"`, `level`
- Metrics: `http_requests_total`, `http_request_duration_seconds`, `db_pool_in_use`, `db_pool_max`,
  `db_pool_waiting`, `process_resident_memory_bytes`, `up`
- Deploy marker: log line `Deployed commit <sha>`

Demo credentials (demo/demo) are for local use only.
