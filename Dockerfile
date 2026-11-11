# A Dockerfile is a recipe for building an "image": a package containing
# our code plus everything it needs to run (Python and the libraries).

# 1. Start from an official small Linux image that already has Python.
FROM python:3.12-slim

# 2. Work inside the /app folder in the image.
WORKDIR /app

# 3. Copy the list of libraries first and install them.
#    Doing this before copying the code lets Docker reuse (cache) this
#    slow step when only our code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy our application code into the image.
COPY app ./app

# 5. The app listens on port 8000 unless the cloud platform sets PORT.
ENV PORT=8000
EXPOSE 8000

# 6. The command that runs when a container starts from this image.
#    0.0.0.0 means "accept connections from outside the container".
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
