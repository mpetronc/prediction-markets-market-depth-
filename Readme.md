# Prediction Market Microstructure: Polymarket vs. Kalshi

This repository studies market quality across Polymarket and Kalshi using matched prediction-market contracts and historical trade and order-book data.

A central part of the project examines market behavior around Polymarket’s reduction of its crypto-market taker delay from 250 ms to 50 ms on 17 August 2026. The analysis compares the week before and after the change using matched Kalshi markets and non-crypto political markets as comparison samples.

## Market samples

- `crypto/CRYPTO_UP_DOWN`: 15-minute cryptocurrency direction markets
- `crypto/CRYPTO_HOURLY`: hourly cryptocurrency price markets
- `MarchMadness`: matched NCAA tournament game-winner markets
- `POLITICAL_EVENTS`: Brazil’s presidential election, Iceland’s EU referendum, and Massachusetts Democratic primaries

## What the pipelines do

- Match equivalent contracts across Polymarket and Kalshi
- Collect and normalize trades and historical order books
- Align prices to the same outcome probability
- Measure spreads, displayed depth, trading activity, volatility, and simulated price impact
- Build pre/post and cross-platform analysis panels
- Produce coverage audits and reproducible summary outputs

Raw and generated datasets are excluded from GitHub because of their size. The repository contains the collection and analysis code, market configurations, tests, documentation, and an `.env.example` file for local setup.

See the README inside each project directory for pipeline-specific instructions.

> Research project in progress.