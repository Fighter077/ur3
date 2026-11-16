# --- Development (Node + Angular CLI) ---
FROM node:22-alpine AS development
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .

# --- Production build ---
FROM development AS build
RUN npm run build

# --- Serve ---
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ur3-flexda/browser /usr/share/nginx/html
EXPOSE 80
