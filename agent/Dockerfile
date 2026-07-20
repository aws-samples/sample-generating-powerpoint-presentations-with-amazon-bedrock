FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY app.py .
COPY tools.py .

# AgentCore Runtime expects the app to listen on port 8080
EXPOSE 8080

# The BedrockAgentCoreApp.run() starts an HTTP server on port 8080
CMD ["python", "app.py"]
