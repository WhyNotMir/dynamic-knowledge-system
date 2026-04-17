# Dev-mode Dockerfile for the Next.js UI. Matches the "hot-reload" style
# of the FastAPI service in the repo root (uvicorn --reload).
#
# We install deps into a layer that's cached between rebuilds, and rely
# on the docker-compose bind-mount to stream source changes into the
# container. `/app/node_modules` is a named volume so the host's macOS
# node_modules (if any) never shadows the Linux ones built here.
FROM node:20-alpine

WORKDIR /app

# Install first — this layer only busts when package.json / lockfile change.
COPY package.json package-lock.json* ./
RUN npm install

# Source is bind-mounted at runtime in docker-compose, so this COPY only
# matters for stand-alone `docker build`.
COPY . .

EXPOSE 3000

# `next dev` is HMR-friendly and picks up file changes from the bind mount.
CMD ["npm", "run", "dev"]
