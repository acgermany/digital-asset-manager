FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# nginx's entrypoint runs scripts in /docker-entrypoint.d before starting: creates TLS certs.
COPY tls/10-dam-tls.sh /docker-entrypoint.d/10-dam-tls.sh
EXPOSE 80 443
