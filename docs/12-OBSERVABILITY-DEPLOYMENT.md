# V3.13 Observability and Deployment

V3.13 adds a read-only dashboard surface for PRE-LIVE diagnostics.

- Backend: `direction_engine_v3.app.server`
- Bind address: `127.0.0.1:8130`
- Nginx proxy: `127.0.0.1:8131`
- Service: `direction-engine-v3-dashboard.service`

The dashboard intentionally exposes no mutation endpoints, no LIVE arming path, and no real
order submission. Readiness is separated from liveness: `/health/live` only proves the process
responds, while `/health/ready` reports trading readiness as failing while LIVE remains disabled.
