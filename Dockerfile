FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY selector.py coverage_map.json.gz impact_index.json.gz /app/
CMD ["python", "/app/selector.py"]
