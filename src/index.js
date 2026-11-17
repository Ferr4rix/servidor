// Servidor do jogo: Express (HTTP) + ENet (UDP) + MongoDB Atlas.
const config = require("./config");
const db = require("./db");
const rooms = require("./rooms");
const storage = require("./storage");
const { createApp } = require("./http/app");
const realtime = require("./realtime/server");

async function main() {
  await db.connect();
  console.log(`[db] ligado ao MongoDB (${config.mongoDb})`);
  await storage.ensureIndexes();
  await storage.migrateOldBlocks();          // room_blocks antigo -> chunks (uma vez)
  if (await db.ensurePublicRoom()) console.log(`[db] sala pública criada (${db.PUBLIC_ROOM.id})`);
  await require("./admin").init();          // salas fechadas/interditadas, balanceamento do painel
  const app = createApp();
  app.listen(config.httpPort, () => console.log(`[http] Express em http://0.0.0.0:${config.httpPort}`));
  await realtime.start(config.enetPort);
  console.log(`[enet] tempo real em udp://0.0.0.0:${config.enetPort}`);
}

async function shutdown(sig) {
  console.log(`[servidor] ${sig}: a gravar...`);
  try {
    await rooms.saveAll();
    await db.close();
  } finally {
    process.exit(0);
  }
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

main().catch((e) => {
  console.error("[servidor] falhou ao arrancar:", e.message);
  process.exit(1);
});
