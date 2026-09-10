# Radal frontend — Vite SPA served by Express under Lambda Web Adapter (LWA)
FROM node:20-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=/api/v1
ENV VITE_API_URL=$VITE_API_URL
# The build context is ./frontend, so there is no .git here: the commit and the
# environment MUST be passed in, or the UI would show "dev" on a real deploy and
# every bug report against it would be untraceable.
ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=$VITE_APP_VERSION
ARG VITE_APP_ENV=dev
ENV VITE_APP_ENV=$VITE_APP_ENV
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/package-lock.json ./package-lock.json
COPY server.cjs ./server.cjs
RUN npm ci --omit=dev
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.7.0 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8080
ENV AWS_LWA_INVOKE_MODE=buffered
ENV AWS_LWA_READINESS_CHECK_PATH=/index.html
EXPOSE 8080
CMD ["node", "server.cjs"]
