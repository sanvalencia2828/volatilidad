# Sistema de Volatilidad

Backend local para escaneo de tokens Solana mediante GMGN y backtesting
walk-forward del motor de decisión.

## Requisitos

- Python 3.11
- `gmgn-cli` autenticado y disponible en `PATH` para datos reales
- Docker Desktop, opcional

Instalar dependencias:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Backend Local

Abrir la interfaz en:

`http://127.0.0.1:8000/`

Iniciar únicamente el backend:

```powershell
.venv\Scripts\python.exe -m uvicorn backend:app --host 127.0.0.1 --port 8000
```

Comprobar estado:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Endpoints disponibles:

- `GET /health`
- `POST /api/run`
- `GET /api/dossier?chain=sol&address=<TOKEN>`

La UI consume el endpoint `/api/dossier` y se sirve desde la raíz (`/`) de la aplicación.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

Los tests matemáticos del executor verifican fees, slippage, PnL positivo y
negativo, y pérdidas por rug pull.

## Backtest

Ejecutar con los valores por defecto, 100 tokens y 7 días:

```powershell
.venv\Scripts\python.exe -m backtest.engine
```

El engine puede usar datos históricos reales de GMGN mediante `HistoricalFetcher`.

Componentes principales:

- `backtest/mock_fetcher.py`: velas sintéticas deterministas.
- `backtest/fetcher.py`: descarga y normaliza velas desde `gmgn-cli`.
- `backtest/engine.py`: ventanas walk-forward de 4 días de entrenamiento y 1 día de test.
- `backtest/executor.py`: ejecución con fees, slippage, TP, SL y timeout.
- `backtest/report.py`: PnL, win rate, profit factor, drawdown y razones de salida.

## Docker

Con Docker Desktop iniciado:

La configuración publica la aplicación en `http://127.0.0.1:8000/`.
