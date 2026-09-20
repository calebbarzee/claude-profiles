# Runs the test suite on Linux, exercising the XDG path branches that never
# execute on macOS. Claude Desktop itself is not installed or launched.
FROM python:3.11-slim

WORKDIR /src
COPY pyproject.toml README.md ./
COPY claude_profiles ./claude_profiles
COPY tests ./tests
COPY docker/smoke.sh ./docker/smoke.sh

RUN pip install --no-cache-dir -e '.[dev]'

CMD ["sh", "-c", "python -m pytest && sh docker/smoke.sh"]
