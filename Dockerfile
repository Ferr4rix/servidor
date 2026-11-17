# Servidor do jogo v0.6 (Express + ENet + MongoDB Atlas + física em Python).
# Construir nesta pasta (servidor/):  docker compose up --build
FROM node:20-bookworm-slim

# A física dos blocos corre no servidor em Python (physics/worker.py), com o
# mesmo código do jogo (pasta jogo/, copiada do cliente por
# sincronizar_do_cliente.py), sem gráficos.
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server
COPY package.json ./
RUN npm install --omit=dev && npm cache clean --force

COPY src ./src
COPY tools ./tools
COPY physics ./physics
# Código do jogo que a física precisa + modelos dos mobs (caixas de colisão)
COPY jogo /app/
# Configurações do jogo: só o servidor as tem (vão para os jogos ao entrar,
# GET /api/config; economia.json nunca sai do servidor).
COPY config/*.json /app/

ENV GAME_DATA_DIR=/app \
    GAME_DIR=/app \
    PHYSICS_PYTHON=python3 \
    PYTHONDONTWRITEBYTECODE=1 \
    HTTP_PORT=3000 \
    ENET_PORT=7777 \
    NODE_ENV=production

EXPOSE 3000/tcp
EXPOSE 7777/udp

USER node
CMD ["node", "src/index.js"]
