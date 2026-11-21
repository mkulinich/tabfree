# The "Edit with Claude" sandbox image (ADR-008): one task per authoring session on ECS Fargate.
# Runs as an unprivileged user with a read-only root filesystem (set in the task definition);
# the security group allows egress only to the model endpoint and the TabFree API.
FROM python:3.12-slim

# The Agent SDK drives the Claude Code CLI, which needs Node.js.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y npm && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 agent
USER agent
ENTRYPOINT ["python", "-m", "tabfree_author_agent"]
