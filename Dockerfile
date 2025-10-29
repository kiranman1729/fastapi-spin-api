# 1. Start with lightweight Python image
FROM python:3.11-slim

# 2. Install system dependencies: gcc (for compiling) and spin (for model checking)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential spin && \
    rm -rf /var/lib/apt/lists/*

# 3. Set working directory inside container
WORKDIR /app

# 4. Copy dependency file and install Python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy your FastAPI app code into the image
COPY . .

# 6. Create output directory (Linux-safe) and set environment variable
ENV OUTPUT_DIR=/app/spin_output
RUN mkdir -p ${OUTPUT_DIR}

# 7. Expose FastAPI port
EXPOSE 8000

# 8. Start the FastAPI app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
