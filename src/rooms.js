// Salas em uso (com pelo menos um jogador ligado): terreno e mobs em memória.
// Partilhado pelo HTTP (Express) e pelo tempo real (ENet). A gravação dos
// blocos é do storage.js (diário a cada 0,25 s + fotos por chunk).
const db = require("./db");
const storage = require("./storage");
const physics = require("./physics/bridge");
const { RoomWorld } = require("./world/roomWorld");
const { MobServer } = require("./mobs/mobServer");

const active = new Map();       // roomId -> { world, mobs, peers:Set }
const loading = new Map();

// ------------------------------------------------------------------ água
// A água da sala e a das bolsas dos parafusos de Arquimedes vão para o
// MongoDB (coleção "room_water", um documento por sala):
//   { _id: sala, c: { "x,y,z": nível 1-8 }, s: { "x,y,z": [a, b] }, updated }
// Grava-se no máximo a cada WATER_SAVE_MS (se mudou), quando a sala fica
// vazia e quando o servidor desliga; ao abrir a sala, volta tudo.
const WATER_SAVE_MS = 3000;
const waterCol = () => db.db.collection("room_water");

async function loadWater(room) {
  try {
    const doc = await waterCol().findOne({ _id: room.id });
    if (!doc) return;
    for (const [k, v] of Object.entries(doc.c || {})) {
      const n = Number(v) | 0;
      if (n > 0 && n <= 8) room.water.set(k, n);
    }
    for (const [k, v] of Object.entries(doc.s || {})) {
      if (Array.isArray(v) && v.some((n) => n > 0)) room.screws.set(k, v.slice(0, 2).map((n) => Number(n) | 0));
    }
  } catch (e) {
    console.warn(`[água] ${room.id}: não li a água gravada: ${e.message}`);
  }
}

async function saveWater(room, force = false) {
  if (!room || (!room.waterDirty && !force)) return;
  room.waterDirty = false;
  // Uma gravação de cada vez por sala, pela ordem: senão uma foto mais velha
  // (do intervalo) podia acabar de gravar depois da mais nova (do fecho).
  const c = Object.fromEntries(room.water);
  const sc = Object.fromEntries(room.screws);
  const empty = !room.water.size && !room.screws.size;
  const run = async () => {
    try {
      if (empty) await waterCol().deleteOne({ _id: room.id });
      else await waterCol().replaceOne({ _id: room.id }, { c, s: sc, updated: new Date() }, { upsert: true });
    } catch (e) {
      room.waterDirty = true;               // tenta outra vez na próxima volta
      console.warn(`[água] ${room.id}: não gravei: ${e.message}`);
    }
  };
  room.waterSaving = (room.waterSaving || Promise.resolve()).then(run);
  await room.waterSaving;
}

// a água ou as bolsas mudaram (server.js chama isto)
function waterChanged(room) {
  if (room) room.waterDirty = true;
}

// sala recomeçada / apagada: a água vai junto
async function clearWater(roomId) {
  const room = active.get(roomId);
  if (room) {
    room.water.clear();
    room.screws.clear();
    room.waterDirty = false;
  }
  try {
    await waterCol().deleteOne({ _id: roomId });
  } catch (e) {
    console.warn(`[água] ${roomId}: ${e.message}`);
  }
}

setInterval(() => {
  for (const room of active.values()) saveWater(room).catch(() => {});
}, WATER_SAVE_MS).unref();

// Lagos (worldgen.js): o buraco sai da semente; a água põe-se UMA vez (ao
// abrir a sala com "lagos" e sem "lagos_agua"), só nas células do lago que
// estão vazias (sem bloco de jogador). Depois é água normal: corre, o balde
// tira, grava-se em room_water.
async function fillLakes(room, data) {
  if (!data.lagos || data.lagos_agua) return;
  const { lakeWater } = require("./world/worldgen");
  let n = 0;
  for (const [k, lv] of lakeWater(data)) {
    const [x, y, z] = k.split(",").map(Number);
    if (room.world.get(x, y, z) || room.water.has(k)) continue;
    room.water.set(k, lv);
    n++;
  }
  data.lagos_agua = true;
  const st = storage.states && storage.states.get(room.id);
  if (st && st.meta) st.meta.lagos_agua = true;
  await db.setRoomFields(room.id, { lagos_agua: true });
  if (n) {
    room.waterDirty = true;
    await saveWater(room, true);
  }
  console.log(`[lagos] ${room.id}: ${n} células de água nos lagos`);
}

async function open(roomId) {
  if (active.has(roomId)) return active.get(roomId);
  if (loading.has(roomId)) return loading.get(roomId);
  const p = (async () => {
    const data = await storage.roomData(roomId);
    if (!data) throw new Error(`sala ${roomId} não existe`);
    const world = new RoomWorld(roomId, data);
    const mobs = new MobServer(roomId, world, (id, fields) => db.setRoomFields(id, fields), data.mobs || []);
    // Sala pública: mobs passivos (spawn.publica); dungeon: esqueletos por andar.
    mobs.publica = !!data.publica || roomId === "room_publica";
    // Zona dos mobs (escolhe a tabela "spawn" de mobs.json): praça = "publica",
    // dungeon (pública de nível 2+) = "dungeon", mundo de jogador = "privada".
    const nivel = Number(data.nivel || 1);
    mobs.zona = data.zona || (mobs.publica ? (nivel >= 2 ? "dungeon" : "publica") : "privada");
    mobs.room = { size_x: data.size_x, size_y: data.size_y, size_z: data.size_z };
    mobs.admin = data.mobs_admin || null;     // ajustes dos moderadores (painel)
    if (mobs.zona === "dungeon") {
      mobs.andares = require("./world/worldgen").floorsOf(data);   // y de cada piso
    }
    mobs.trimToMax();
    // Projéteis dos mobs que batem num bloco: a física (Python) recebe-o como
    // um golpe — acumula dano, empurra ou quebra conforme a força.
    mobs.onBlockHit = (cell, damage, dir) =>
      physics.send({ op: "hit", room: roomId, by: null, cell, damage, dir });
    // water: a água da sala (chave "x,y,z" -> nível 1-8); screws: água nas
    // bolsas dos parafusos de Arquimedes ("x,y,z" -> [a, b]). Quem simula é
    // um jogo (ver engine/water.py / engine/screw.py); o servidor guarda o
    // estado e grava-o no MongoDB ("room_water"), por isso volta ao reabrir.
    const room = { id: roomId, world, mobs, peers: new Set(), water: new Map(), screws: new Map(),
                   waterDirty: false };
    await loadWater(room);
    await fillLakes(room, data);
    // fome/sede dos mobs: a água da sala e comer capim (worker: op "graze")
    mobs.waterMap = () => room.water;
    mobs.onGraze = (cell) => physics.send({ op: "graze", room: roomId, cell });
    active.set(roomId, room);
    physics.send({ op: "open", room: roomId, data });   // a física da sala corre no Python
    if (room.water.size) physics.send({ op: "water", room: roomId, changes: [...room.water] });  // para o fogo
    console.log(`[salas] ${roomId} aberta (${world.mods.size} alterações, ${mobs.mobs.size} mobs, ${room.water.size} células de água)`);
    return room;
  })();
  loading.set(roomId, p);
  try {
    return await p;
  } finally {
    loading.delete(roomId);
  }
}

function get(roomId) { return active.get(roomId) || null; }

// O diário já grava sozinho a cada 0,25 s; isto força a gravação agora.
async function flushBlocks() {
  await storage.flushAll();
}

// Muda um bloco: na memória da sala (mobs/colisões) e no diário (gravado).
async function setBlock(roomId, key, value) {
  const room = active.get(roomId);
  if (room) room.world.set(key, value);
  if (!storage.setBlockLoaded(roomId, key, value)) await storage.setBlock(roomId, key, value);
  return true;
}

async function closeIfEmpty(roomId) {
  const room = active.get(roomId);
  if (!room || room.peers.size || room.closing) return;
  room.closing = true;
  try {
    const st = storage.states.get(roomId);
    if (st) await storage.snapshot(st);      // sala vazia: foto das chunks já
    await room.mobs.save();
    await saveWater(room);
    // Alguém entrou enquanto se gravava (sair e voltar logo, trocar de
    // mundo): a sala continua aberta. Antes era tirada de "active" com o
    // jogador lá dentro, e a água e os blocos dele já não iam a lado nenhum.
    if (room.peers.size || active.get(roomId) !== room) return;
    active.delete(roomId);
    physics.send({ op: "close", room: roomId });
    console.log(`[salas] ${roomId} fechada (água gravada: ${room.water.size} células)`);
  } finally {
    room.closing = false;
  }
}

async function saveAll() {
  await storage.snapshotAll();
  for (const room of active.values()) {
    await room.mobs.save();
    await saveWater(room);
  }
}

module.exports = { open, get, setBlock, flushBlocks, closeIfEmpty, saveAll, active, waterChanged, clearWater, saveWater };
