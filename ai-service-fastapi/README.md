# AI Intelligence Engine

## Purpose
This container is a FastAPI microservice dedicated to running our AI classification models. It abstracts away the heavy lifting of machine learning from the orchestrator.

## Endpoints
- `POST /analyze`: Accepts the raw content of an email and returns a structured JSON response indicating:
  - Threat Level (Safe, Phishing, Spam)
  - Urgency (Critical, Normal, Low)
  - Category (Promotion, Update, Work)

## Development Approach
Written in Python and FastAPI to easily integrate with libraries like PyTorch, Hugging Face `transformers`, or external LLM APIs (like Google Gemini).

To run locally without Docker:
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```
