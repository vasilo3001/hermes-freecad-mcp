FROM python:3.11-slim

# No system deps needed — pure Python MCP server
# FreeCAD runs on the host, container only runs mcp_server.py

WORKDIR /app

# Install MCP SDK
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the MCP server
COPY mcp_server.py .

# Non-root user
RUN adduser --disabled-password --no-create-home mcp \
    && chown -R mcp:mcp /app
USER mcp

# Environment: where to find FreeCAD on the host
# host.docker.internal works on Docker Desktop (macOS/Windows)
# On Linux, use --add-host=host.docker.internal:host-gateway
ENV FREECAD_HOST=host.docker.internal
ENV FREECAD_PORT=9876

# HTTP (streamable) mode — persistent server for multi-agent access
# Portainer stack maps host port -> 8000
EXPOSE 8000
ENTRYPOINT ["python3", "mcp_server.py", "--http", "--host", "0.0.0.0", "--port", "8000"]
