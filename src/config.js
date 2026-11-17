// Configuração lida do ambiente (.env). Nada de senhas no código.
const path = require("path");
try {
  const dotenv = require("dotenv");
  dotenv.config({ path: process.env.ENV_FILE || path.resolve(__dirname, "../.env") });
  dotenv.config();   // também aceita um .env na pasta server/
} catch (e) {
  // sem dotenv (ex.: testes): usa só as variáveis de ambiente
}

const ROOT = process.env.GAME_DATA_DIR || path.resolve(__dirname, "../jogo");   // no Docker: /app

module.exports = {
  mongoUri: process.env.MONGODB_URI || "",
  mongoDb: process.env.MONGODB_DB || "jogo",
  httpPort: parseInt(process.env.HTTP_PORT || "3000", 10),
  enetPort: parseInt(process.env.ENET_PORT || "7777", 10),
  sessionHours: parseFloat(process.env.SESSION_HOURS || "72"),
  // Ficheiros de dados partilhados com o cliente (blocks.json, mobs.json, modelos).
  dataDir: ROOT,
  blockSize: 2.0,
};
