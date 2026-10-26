FROM minusops-native-sim-openrouter-deps:df3db64 AS source
USER root
WORKDIR /app
COPY patches/ /app/
COPY dev-next.config.mjs /app/apps/sim/next.config.mjs
COPY patch-manifest.json typecheck.cjs build.mjs /overlay/
RUN bun build apps/sim/next.config.ts --target=node --format=esm --packages=bundle --outfile=apps/sim/next.config.native.mjs \
    && mv apps/sim/next.config.ts apps/sim/next.config.source.ts \
    && bun build packages/sim-cli/src/runtime.ts --target=node --format=esm --packages=bundle --outfile=packages/sim-cli/dist/runtime.js \
    && cd apps/sim && bun run build:sandbox-bundles && bun run build:xlsx-worker
ENV NEXT_PUBLIC_APP_URL=http://localhost:3010 NEXT_PUBLIC_SOCKET_URL=http://localhost:3012 \
    NEXT_PUBLIC_CHAT_DISABLED=false MINUS_OPENROUTER_CHAT=true \
    NODE_ENV=production NODE_OPTIONS=--max-old-space-size=12288 \
    NEXT_BUILD_SKIP_TYPE_CHECK=1 NEXT_TELEMETRY_DISABLED=1 DOCKER_BUILD=0 \
    DATABASE_URL=postgresql://127.0.0.1:5432/native_build RAYON_NUM_THREADS=2
RUN rm -rf /app/apps/sim/.next

FROM source AS compiled
RUN node /overlay/build.mjs && \
    chown -R nextjs:nodejs /app/apps/sim/.next

FROM compiled AS runtime
USER nextjs
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 NODE_OPTIONS=--max-old-space-size=4096
CMD ["node", "node_modules/next/dist/bin/next", "start", "apps/sim", "--hostname", "0.0.0.0", "--port", "3000"]
