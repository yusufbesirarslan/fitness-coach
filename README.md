# AxisAI — Fitness Coach

AxisAI is an AI-powered fitness platform designed to bring training, nutrition,
progress tracking, and adaptive coaching into one system.

This repository contains the core backend and web application powering AxisAI.

> AxisAI is currently under active development.

## What AxisAI is building

AxisAI is designed around a simple idea:

**fitness data should lead to useful decisions, not just more dashboards.**

Current product areas include:

- AI-assisted fitness coaching
- Personalized training plan generation
- Adaptive training-plan updates
- Workout and training progress tracking
- Nutrition logging and food discovery
- Progress insights and history
- Physique progress and Pump Checks
- Mobile-facing authenticated APIs
- Recovery and training-context integration

## Architecture

The application is primarily built with:

- Python / Flask
- SQLAlchemy
- PostgreSQL
- Redis
- AWS infrastructure and managed services
- Docker
- GitHub Actions

The codebase follows server-authoritative domain boundaries for sensitive
operations such as authentication, training-plan mutation, progress
interpretation, nutrition persistence, and user-owned media.

## Development

### Requirements

- Python 3.11
- PostgreSQL
- Redis where required by the selected development path

Install development dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
