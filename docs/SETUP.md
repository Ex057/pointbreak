# Developer setup notes

The supported local workflow is documented in the repository root
[README.md](../README.md). The current public release uses FastAPI, React/Vite,
the threaded Deriv WebSocket client, Yahoo fallback data, and SQLite. PostgreSQL,
Redis, Firebase, authentication, and the Android client are future deployment
tracks, not prerequisites for running this web release.

For a clean offline smoke test:

```bash
cp .env.example .env
sed -i.bak 's/^DATA_SOURCE=.*/DATA_SOURCE=fixture/' .env
./start.sh setup
./start.sh test
./start.sh start
```
