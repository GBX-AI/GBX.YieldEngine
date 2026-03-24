# Yield Engine

Portfolio income system for Indian F&O markets. Connects to Zerodha Kite Connect API for live market data, scans for options strategies (covered calls, cash-secured puts, credit spreads, collars), detects arbitrage opportunities, and tracks trades with P&L analytics.

## Architecture

- **Backend**: Python 3.12 / Flask / Gunicorn (port 8000)
- **Frontend**: React 18 / Vite / Nginx (port 3000)
- **Database**: SQLite (file-based)
- **Market Data**: Kite Connect API
- **Deployment**: Azure Container Apps

## Local Development

```bash
# With Docker
docker-compose up -d

# Without Docker
cd backend && pip install -r requirements.txt && python app.py
cd frontend && npm install && npm run dev
```

## Deployment

Pushes to `main` trigger automatic deployment via GitHub Actions to Azure Container Apps.

For manual deployment:
```bash
bash deploy/azure-deploy.sh [TAG]
```
