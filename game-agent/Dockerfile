FROM node@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205
WORKDIR /opt/runtime
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && npx playwright install --with-deps chromium && npm cache clean --force
COPY guard.mjs /opt/guard.mjs
COPY models.json /opt/pi-profile/models.json
ENV PATH="/opt/runtime/node_modules/.bin:${PATH}" HOME=/tmp PI_CODING_AGENT_DIR=/opt/pi-profile PI_OFFLINE=1 PI_TELEMETRY=0 PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
RUN chmod -R a+rX /root/.cache/ms-playwright && mv /root/.cache/ms-playwright /opt/browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
USER 1000:1000
WORKDIR /workspace
CMD ["pi", "--version"]
