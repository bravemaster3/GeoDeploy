FROM node:20-alpine AS development
WORKDIR /app
COPY package*.json ./
# npm install (not ci) so adding a dep to package.json doesn't require regenerating the lock from a
# machine with Node — there's none here (UI builds in Docker). It reconciles an out-of-sync lock.
RUN npm install --no-audit --no-fund
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
# npm install (not ci) so adding a dep to package.json doesn't require regenerating the lock from a
# machine with Node — there's none here (UI builds in Docker). It reconciles an out-of-sync lock.
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
