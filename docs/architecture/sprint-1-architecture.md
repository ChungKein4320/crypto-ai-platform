# Sprint 1 Architecture

## 1. Sprint Goal

Build and validate a minimal cloud-based crypto data pipeline that collects Binance BTC/USDT 5m data, stores it persistently, and serves it through a public website without requiring the user's laptop to run continuously.

## 2. Data Flow

```text
Binance Public API
        ↓
Collector
        ↓
Cloud Scheduler
        ↓
Persistent Cloud Database
        ↓
Public Dashboard
```

## 3. System Components

### 3.1 Binance

Source of market data.

* Exchange: Binance
* Market: BTC/USDT
* Market type: Spot
* Timeframe: 5m
* API symbol: BTCUSDT

### 3.2 Collector

Python program responsible for:

* Fetching Binance BTC/USDT 5m market data.
* Determining the latest data that needs to be inserted or updated.
* Handling the currently open candle when necessary.
* Writing data to the database.

The collector should use incremental ingestion instead of downloading the entire historical dataset on every run.

### 3.3 Cloud Scheduler

Responsible for triggering the collector periodically.

Target:

* Run approximately every 5 minutes.
* Trigger data ingestion without requiring the user's laptop to remain on.

### 3.4 Persistent Cloud Database

Responsible for long-term storage of collected market data.

Requirements:

* Persistent storage.
* Preserve historical candles.
* Prevent duplicate candles.
* Support incremental data insertion/update.

### 3.5 Public Dashboard

Responsible only for reading and displaying data stored in the database.

The dashboard is not responsible for collecting Binance data.

Initial dashboard scope:

* BTC/USDT
* Binance Spot
* 5m timeframe
* Current/latest price
* Candlestick chart
* Volume
* Basic LIVE/OFFLINE status

## 4. Core Data Principles

### 4.1 Historical Data Preservation

Existing candles must not be deleted when new candles arrive.

Example:

```text
100 candles
→ 101 candles
→ 102 candles
→ 103 candles
```

### 4.2 Open Candle Handling

A 5-minute candle may remain open while it is being formed.

The system should:

* Update the currently open candle when necessary.
* Keep the candle after it becomes closed.
* Avoid creating duplicate candles for the same candle timestamp.

### 4.3 Incremental Ingestion

The collector should not fetch the complete historical dataset on every scheduled execution.

Each execution should retrieve only the data required to update the database.

## 5. Local vs Cloud Responsibility

### Local

Local development is used for:

* Coding
* Testing
* Debugging
* Local execution

### Cloud

The cloud environment is responsible for:

* Scheduled data collection
* Persistent data storage
* Public website hosting

The laptop must not be a required component for the production-like data pipeline.

## 6. Sprint 1 Scope

Included:

* Binance BTC/USDT Spot
* 5m OHLCV data
* Local data pipeline
* Persistent cloud database
* Scheduled cloud ingestion
* Public dashboard
* Dataset growth verification

## 7. Out of Scope

The following are intentionally excluded from Sprint 1:

* Binance WebSocket
* Real-time trading
* Auto trading
* Price prediction
* Multiple exchanges
* ETH/SOL
* On-chain data
* Sentiment analysis
* News NLP
* Authentication
* User accounts
* Complex backend architecture
* Docker
* Kubernetes
* Redis
* Microservices
* Complex technical indicators

## 8. Sprint 1 Target State

The Sprint is considered successful when the following flow works end-to-end:

```text
Binance BTC/USDT 5m
        ↓
Cloud Scheduler
        ↓
Collector
        ↓
Persistent Cloud Database
        ↓
Public Website
```

The system must continue collecting and storing new data even when the user's laptop is turned off.
