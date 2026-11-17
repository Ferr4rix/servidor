// Acesso ao MongoDB Atlas. Coleções:
//   players     { _id: "#id", usuario, senha_hash, nome, link_room_properties, ... }
//   rooms       { _id: "room_x", nome, seed, size_x/y/z, layers, links, glue_faces, containers, mobs }
//   chunks      { _id: "sala|cx|cz", room, cx, cz, b: {"x,y,z": valor} }  (ver storage.js)
//   journal     { room, seq, at, ops }  (diário das mudanças ainda não postas nas chunks)
//   sessions    { _id: token, player_id, expires }
const crypto = require("crypto");
const { MongoClient } = require("mongodb");
const config = require("./config");

const PRIVATE = ["usuario", "senha_hash", "_id"];
let client = null;
let db = null;

async function connect() {
  if (db) return db;
  if (!config.mongoUri) {
    throw new Error("MONGODB_URI não definido: copie .env.example para .env e preencha");
  }
  client = new MongoClient(config.mongoUri, { serverSelectionTimeoutMS: 10000 });
  await client.connect();
  db = client.db(config.mongoDb);
  await db.collection("players").createIndex({ usuario: 1 }, { unique: true });
  await db.collection("sessions").createIndex({ expires: 1 }, { expireAfterSeconds: 0 });
  return db;
}

async function close() {
  if (client) await client.close();
  client = null;
  db = null;
}

const col = (name) => db.collection(name);

// ---------------------------------------------------------------- jogadores
function publicPlayer(doc) {
  const out = {};
  for (const [k, v] of Object.entries(doc || {})) {
    if (!PRIVATE.includes(k)) out[k] = v;
  }
  out.usuario = doc.usuario;
  return out;
}

async function findPlayerByUser(usuario) {
  return col("players").findOne({ usuario: String(usuario) });
}

async function getPlayer(id) {
  return col("players").findOne({ _id: String(id) });
}

async function savePlayer(id, data) {
  const clean = {};
  for (const [k, v] of Object.entries(data || {})) {
    if (!PRIVATE.includes(k) && !k.startsWith("$") && !k.includes(".")) clean[k] = v;
  }
  await col("players").updateOne({ _id: String(id) }, { $set: clean });
}

async function setPasswordHash(id, hash) {
  await col("players").updateOne({ _id: String(id) }, { $set: { senha_hash: hash } });
}

async function createPlayer(usuario, senhaHash, nome) {
  const id = "#" + crypto.randomBytes(4).toString("hex");
  await col("players").insertOne({
    _id: id, usuario, senha_hash: senhaHash, nome: nome || usuario,
    link_room_properties: [],
  });
  return id;
}

// ---------------------------------------------------------------- sessões
async function createSession(playerId) {
  const token = crypto.randomBytes(24).toString("hex");
  const expires = new Date(Date.now() + config.sessionHours * 3600 * 1000);
  await col("sessions").insertOne({ _id: token, player_id: playerId, expires });
  return token;
}

// Uma sessão por conta: ao entrar, as outras (tokens antigos) deixam de valer.
async function deleteSessionsOf(playerId, exceptToken = null) {
  const q = { player_id: playerId };
  if (exceptToken) q._id = { $ne: String(exceptToken) };
  await col("sessions").deleteMany(q);
}

async function deleteSession(token) {
  if (token) await col("sessions").deleteOne({ _id: String(token) });
}

// A sala própria do jogador (a que ele criou ao registar-se). Mundos antigos
// sem dono gravado: o primeiro mundo não público da lista dele.
async function findOwnRoom(player) {
  const own = await col("rooms").findOne({ owner: player._id, publica: { $ne: true } },
    { projection: { _id: 1 } });
  if (own) return own._id;
  for (const id of player.link_room_properties || []) {
    const r = await col("rooms").findOne({ _id: String(id) }, { projection: { publica: 1 } });
    if (r && !r.publica) return String(id);
  }
  return null;
}

async function resolveSession(token) {
  if (!token) return null;
  const s = await col("sessions").findOne({ _id: String(token) });
  if (!s || s.expires < new Date()) return null;
  return s.player_id;
}

// ---------------------------------------------------------------- salas
// Só o documento da sala (nome, dono, semente...). Os blocos vêm do
// storage.js (storage.roomData).
async function getRoom(id) {
  const room = await col("rooms").findOne({ _id: String(id) });
  if (!room) return null;
  const out = { ...room };
  delete out._id;
  return out;
}

// Campos "diretos" da sala (nome, dono, mobs). Os campos do mundo (baús,
// cola, almas) passam pelo diário: storage.setFields.
async function setRoomFields(roomId, fields) {
  const set = {};
  for (const [k, v] of Object.entries(fields || {})) {
    if (k === "modifications" || k.startsWith("$") || k.includes(".") || k === "_id") continue;
    set[k] = v;
  }
  if (Object.keys(set).length) {
    await col("rooms").updateOne({ _id: String(roomId) }, { $set: set });
  }
}

async function createRoom(doc) {
  let id;
  do {
    id = "room_" + crypto.randomBytes(4).toString("hex");
  } while (await col("rooms").findOne({ _id: id }, { projection: { _id: 1 } }));
  await col("rooms").insertOne({ _id: id, ...doc });
  return id;
}

// Todos os mundos com o nome e o dono (para o portal: ir aos mundos dos outros).
async function listAllRooms(limit = 100) {
  // salas fechadas ou interditadas pelos moderadores não aparecem
  const open = { fechada: { $ne: true }, $or: [{ interditada: null }, { interditada: { $exists: false } }] };
  const rooms = await col("rooms").find({ publica: { $ne: true }, ...open },
    { projection: { nome: 1, owner: 1, publica: 1, nivel: 1 } }).limit(limit).toArray();
  // As salas públicas vêm sempre (fora do limite).
  rooms.unshift(...await col("rooms").find({ publica: true, ...open },
    { projection: { nome: 1, owner: 1, publica: 1, nivel: 1 } }).toArray());
  const ids = rooms.map((r) => r._id);
  const players = await col("players").find({ link_room_properties: { $in: ids } },
    { projection: { nome: 1, usuario: 1, link_room_properties: 1 } }).toArray();
  const byId = new Map(players.map((p) => [p._id, p]));
  return rooms.map((r) => {
    let owner = r.owner ? byId.get(r.owner) : null;
    if (!owner) owner = players.find((p) => (p.link_room_properties || []).includes(r._id)) || null;
    if (r.publica) return { id: r._id, nome: r.nome || "Sala pública", owner_id: null,
                            dono: "todos", publica: true, nivel: Math.max(1, Number(r.nivel || 1) | 0) };
    return { id: r._id, nome: r.nome || null, owner_id: owner ? owner._id : null,
             dono: owner ? (owner.nome || owner.usuario) : "?" };
  });
}

// Sala pública (nível 1): todos podem entrar; 200x200 com um minoculus no meio.
const PUBLIC_ROOM = {
  id: "room_publica", nome: "Praça pública", seed: 2026,
  size_x: 200, size_y: 20, size_z: 200, layers: 3, praca_raio: 6, nivel: 1,
  // lagos escavados no chão e cheios de água (worldgen.js lakesOf; rooms.js fillLakes)
  lagos: { quantidade: 7, raio: [4, 9] },
  // plantas já na criação (chance por bloco de grama; worldgen.js plantAt);
  // depois crescem mais com o tempo (engine/tufts.py, no worker)
  plantas: { "capim": 0.008, "flor lunar": 0.0025, "berco solar": 0.0003 },
};
// Dungeons = salas públicas de nível 2+: só se chega pelo minoculus de uma
// sala pública do nível anterior (ver access.js). Dungeon 1: caixa oca de
// pedra dura 200x98x200 (geração "caixa", worldgen.js), paredes forradas
// por dentro de pedra com fragmentos solares, um piso de pedra em y=1 e a
// cada 16 de altura (6 andares de 16), em cada andar um labirinto de paredes
// de pedra do chão ao teto, 3-4 escadas em caracol por andar para o de cima
// e um minoculus no meio do andar de baixo, numa clareira.
// "versao": ao mudar a geração, sobe-se o número e o servidor refaz a
// dungeon que está no banco (ver ensurePublicRoom).
const DUNGEON_ROOMS = [{
  id: "room_dungeon_1", nome: "Dungeon 1", seed: 1, versao: 4,
  size_x: 200, size_y: 102, size_z: 200, layers: 3, nivel: 2,
  geracao: "caixa", material: "pedra dura",
  revestimento: { bloco: "pedra", fragmento: "fragmento solar", chance: 0.004 },
  camadas: { a_cada: 10, inicio: 1, bloco: "pedra", fragmento: "fragmento solar", chance: 0.0015 },
  escadas: { por_andar: [3, 4], bloco: "pedra", luz: "fragmento solar" },
  labirinto: { celula: 14, aberto_extra: 0.2, clareira: 8, bloco: "pedra",
               fragmento: "fragmento solar", chance: 0.003 },
  minoculus_y: 2,
  baus: { bloco: "bau grande", por_andar: 5, espaco: 8 },
}];
// Santuário (nível 3): só se entra pelo minoculus da Dungeon 1 (nível 2).
// Sala branca e vazia — chão liso de pedra branca, céu branco, luz cheia —
// onde se enfrenta o anjo (mobs.json: anjo.spawn.santuario, 1 de cada vez).
const SANCTUARY_ROOMS = [{
  id: "room_santuario", nome: "Santuário do Anjo", seed: 3,
  size_x: 80, size_y: 40, size_z: 80, layers: 3, nivel: 3,
  geracao: "branca", material: "pedra branca", zona: "santuario",
  ceu: "#f4f5f8", ambiente: 1.0,
  minoculus_y: 4,
}];
const GROUND_Y = 3;   // = worldgen.js / CRUD_room.py

async function ensureRoom(p, minoculusY) {
  if (await col("rooms").findOne({ _id: p.id }, { projection: { _id: 1 } })) return false;
  const { id, ...doc } = p;
  await col("rooms").insertOne({ _id: id, ...doc, publica: true, owner: null });
  const cx = Math.floor(p.size_x / 2), cz = Math.floor(p.size_z / 2);
  const storage = require("./storage");
  await storage.setBlock(id, `${cx},${minoculusY},${cz}`, "minoculus-grump");
  const st = storage.states.get(id);
  if (st) await storage.snapshot(st);
  return true;
}

// Pisos da dungeon (y em células): os de "camadas" (inicio + a_cada + tambem).
function dungeonFloors(d) {
  return require("./world/worldgen").floorsOf(d);
}

// Baús grandes espalhados pelos andares (2x2x2 cada): sítios do labirinto com
// chão por baixo e ar nas 2 células de cima, longe uns dos outros e da
// clareira do minoculus. Sorteio fixo pela semente (sempre os mesmos sítios).
function dungeonChestSpots(d) {
  const spec = d.baus;
  if (!spec) return [];
  const { baseBlock, cellHash } = require("./world/worldgen");
  const at = (x, y, z) => baseBlock(d, new Map(), x, y, z);
  const n = Number(spec.por_andar || 5);
  const gap = Number(spec.espaco || 8);
  const cx = Math.floor(d.size_x / 2), cz = Math.floor(d.size_z / 2);
  const out = [];
  for (const fy of dungeonFloors(d)) {
    const here = [];
    for (let k = 0; k < 4000 && here.length < n; k++) {
      const x = 3 + Math.floor(cellHash((d.seed | 0) + 40, k, fy, 1) * (d.size_x - 7));
      const z = 3 + Math.floor(cellHash((d.seed | 0) + 41, k, fy, 2) * (d.size_z - 7));
      if (Math.abs(x - cx) <= 10 && Math.abs(z - cz) <= 10) continue;       // clareira
      if (here.some(([hx, , hz]) => Math.abs(hx - x) < gap && Math.abs(hz - z) < gap)) continue;
      let ok = true;
      for (let dx = 0; dx < 2 && ok; dx++) {
        for (let dz = 0; dz < 2 && ok; dz++) {
          if (!at(x + dx, fy, z + dz)) ok = false;                        // chão
          else if (at(x + dx, fy + 1, z + dz) || at(x + dx, fy + 2, z + dz)) ok = false;   // ar
        }
      }
      if (ok) here.push([x, fy + 1, z]);
    }
    out.push(...here);
  }
  return out;
}

async function placeDungeonChests(d) {
  const spots = dungeonChestSpots(d);
  if (!spots.length) return 0;
  const storage = require("./storage");
  for (const [x, y, z] of spots) await storage.setBlock(d.id, `${x},${y},${z}`, d.baus.bloco);
  const st = storage.states.get(d.id);
  if (st) await storage.snapshot(st);
  await col("rooms").updateOne({ _id: d.id }, { $set: { baus_v1: true } });
  return spots.length;
}

// Dungeon gravada com uma geração antiga ("versao" diferente): apaga o que
// foi mexido nela (blocos, baús, mobs gravados) e grava a configuração nova;
// os blocos de base saem da geração nova sozinhos.
async function upgradeDungeon(d) {
  const doc = await col("rooms").findOne({ _id: d.id }, { projection: { versao: 1 } });
  if (!doc || Number(doc.versao || 1) === Number(d.versao || 1)) return false;
  const storage = require("./storage");
  await storage.reset(d.id);
  storage.states.delete(d.id);                  // relê a configuração nova
  const { id, ...cfg } = d;
  await col("rooms").updateOne({ _id: id }, { $set: cfg, $unset: { baus_v1: "", mobs: "" } });
  const cx = Math.floor(d.size_x / 2), cz = Math.floor(d.size_z / 2);
  await storage.setBlock(id, `${cx},${d.minoculus_y || 1},${cz}`, "minoculus-grump");
  const st = storage.states.get(id);
  if (st) await storage.snapshot(st);
  console.log(`[dungeon] ${id}: refeita com a geração v${d.versao}`);
  return true;
}

async function ensurePublicRoom() {
  const made = await ensureRoom(PUBLIC_ROOM, GROUND_Y + 1);
  // sala pública que já existia sem lagos: passa a tê-los (a água vem ao abrir)
  if (!made && PUBLIC_ROOM.lagos) {
    const doc = await col("rooms").findOne({ _id: PUBLIC_ROOM.id }, { projection: { lagos: 1 } });
    if (doc && JSON.stringify(doc.lagos || null) !== JSON.stringify(PUBLIC_ROOM.lagos)) {
      await setRoomFields(PUBLIC_ROOM.id, { lagos: PUBLIC_ROOM.lagos, lagos_agua: false });
      console.log("[lagos] sala pública: lagos adicionados");
    }
    const doc2 = await col("rooms").findOne({ _id: PUBLIC_ROOM.id }, { projection: { plantas: 1 } });
    if (doc2 && JSON.stringify(doc2.plantas || null) !== JSON.stringify(PUBLIC_ROOM.plantas)) {
      await setRoomFields(PUBLIC_ROOM.id, { plantas: PUBLIC_ROOM.plantas });
      console.log("[plantas] sala pública: plantas da criação ligadas");
    }
  }
  for (const d of SANCTUARY_ROOMS) {
    if (!await ensureRoom(d, d.minoculus_y)) {
      // já existia: mantém céu/luz/zona em dia com a configuração
      const { id, ...cfg } = d;
      await col("rooms").updateOne({ _id: id }, { $set: { ceu: cfg.ceu, ambiente: cfg.ambiente, zona: cfg.zona } });
    }
  }
  for (const d of DUNGEON_ROOMS) {
    await ensureRoom(d, d.minoculus_y || 1);                  // em cima do piso
    await upgradeDungeon(d);
    // baús: na criação e também numa dungeon que já existia sem eles
    const doc = await col("rooms").findOne({ _id: d.id }, { projection: { baus_v1: 1 } });
    if (doc && !doc.baus_v1) {
      const n = await placeDungeonChests(d);
      if (n) console.log(`[dungeon] ${d.id}: ${n} baús grandes espalhados`);
    }
  }
  return made;
}

async function anyRoom() {
  return col("rooms").findOne({}, { projection: { size_x: 1, size_y: 1, size_z: 1 } });
}

async function roomNames(ids) {
  const docs = await col("rooms").find({ _id: { $in: ids.map(String) } },
    { projection: { nome: 1 } }).toArray();
  const map = {};
  for (const d of docs) map[d._id] = d.nome;
  return map;
}

module.exports = {
  connect, close, publicPlayer, findPlayerByUser, getPlayer, savePlayer, setPasswordHash,
  createPlayer, createSession, resolveSession, deleteSessionsOf, deleteSession, findOwnRoom, getRoom, setRoomFields, createRoom, anyRoom, roomNames, listAllRooms, ensurePublicRoom, PUBLIC_ROOM, DUNGEON_ROOMS, SANCTUARY_ROOMS, dungeonChestSpots, dungeonFloors,
  ensureRoom, placeDungeonChests, GROUND_Y,
  get db() { return db; },
};
