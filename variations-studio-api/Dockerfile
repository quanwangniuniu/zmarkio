FROM node:20-bullseye

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package*.json ./
# Keep tsx and the source files for the one-off migration CLI.
RUN npm ci

COPY . .
RUN npm run prisma:generate && npm run build
RUN chown -R node:node /app

USER node
ENV NODE_ENV=production
ENV PORT=3010
ENV HOSTNAME=0.0.0.0
EXPOSE 3010

CMD ["npm", "start"]
