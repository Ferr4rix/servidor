// Importa os saves locais (rooms.json e players.json da v0.4) para o MongoDB.
//   docker compose run --rm servidor node tools/import_json.js /dados
// ou, sem Docker, na pasta server/:
//   npm run importar -- ..
// (o argumento é a pasta onde estão rooms.json e players.json)
const fs = require("fs");
const path = require("path");
const db = require("../src/db");
const storage = require("../src/storage");

async function main() {
  const dir = path.resolve(process.argv[2] || path.join(__dirname, "..", ".."));
  const rooms = JSON.parse(fs.readFileSync(path.join(dir, "rooms.json"), "utf8"));
  const players = JSON.parse(fs.readFileSync(path.join(dir, "players.json"), "utf8"));
  await db.connect();
  await storage.ensureIndexes();
  for (const [id, room] of Object.entries(rooms)) {
    const { modifications = {}, ...meta } = room;
    await db.db.collection("rooms").updateOne({ _id: id }, { $set: meta }, { upsert: true });
    await storage.reset(id);                 // limpa chunks/diário (e baús, cola...)
    const world = {};                        // baús, cola, almas: passam pelo diário
    for (const f of ["links", "containers", "glue_faces", "soul_banks"]) {
      if (meta[f] !== undefined) world[f] = meta[f];
    }
    await storage.setFields(id, { modifications, ...world });
    await storage.snapshot(storage.states.get(id));
    console.log(`sala ${id}: ${Object.keys(modifications).length} blocos alterados`);
  }
  for (const [id, p] of Object.entries(players)) {
    if (!p.usuario) continue;
    await db.db.collection("players").updateOne({ _id: id }, { $set: p }, { upsert: true });
    console.log(`jogador ${p.usuario} (${id})`);
  }
  await db.close();
  console.log("importação concluída");
}

main().catch((e) => { console.error(e.message); process.exit(1); });
