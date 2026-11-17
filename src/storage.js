// Gravação do mundo: diário em lote + fotos por chunk.
//
// Tudo o que muda no mundo (blocos, baús, cola, almas...) entra na hora num
// DIÁRIO na memória. A cada JOURNAL_MS (0,25 s) as mudanças juntas de cada
// sala vão para o Mongo numa só escrita (coleção "journal", confirmada pela
// maioria das réplicas). Se o servidor cair, perde-se no máximo isso.
//
// De SNAPSHOT_MS em SNAPSHOT_MS (ou quando o diário de uma sala cresce
// demais, ou a sala esvazia, ou o servidor desliga) as chunks alteradas são
// gravadas inteiras ("chunks", uma por 16x16 colunas) e o diário que já está
// nelas é apagado. Ao abrir uma sala: lê as chunks e reaplica o diário por
// cima, por ordem -> o mundo volta exatamente como estava.
//
// Só se guarda o que difere do mundo gerado: um bloco que volta a ser o que
// era (ex.: põe e tira) deixa de ocupar espaço.
//
//   chunks  { _id: "sala|cx|cz", room, cx, cz, b: { "x,y,z": valor }, seq, updated }
//   journal { room, seq, at, ops: [["b", "x,y,z", valor] | ["f", campo, valor]] }
const db = require("./db");
const { generateTrees, baseBlock, key: cellKey } = require("./world/worldgen");

const CHUNK = 16;
const JOURNAL_MS = 250;
const SNAPSHOT_MS = 60 * 1000;
const SNAPSHOT_OPS = 2000;          // diário de uma sala com mais que isto: foto já
const IDLE_UNLOAD_MS = 5 * 60 * 1000;
// Campos da sala que são "mundo" (passam pelo diário). O resto (nome, dono,
// mobs...) continua a ser gravado direto no documento da sala.
const WORLD_FIELDS = new Set(["links", "containers", "glue_faces", "soul_banks", "auto_cola_limpa", "fogo", "corpos"]);
const MAJORITY = { writeConcern: { w: "majority" } };

const col = (name) => db.db.collection(name);
const states = new Map();           // roomId -> estado na memória
const loading = new Map();
let seqCounter = 0;
let lastSeq = 0;

function nextSeq() {
  // crescente mesmo entre reinícios do servidor (relógio x 1000 + contador)
  const s = Math.max(lastSeq + 1, Date.now() * 1000 + (seqCounter++ % 1000));
  lastSeq = s;
  return s;
}

function chunkOf(k) {
  const [x, , z] = String(k).split(",").map(Number);
  return [Math.floor(x / CHUNK), Math.floor(z / CHUNK)];
}

function canonical(value) {
  if (value && typeof value === "object") return value.type || null;
  return value || null;
}

async function ensureIndexes() {
  await col("chunks").createIndex({ room: 1 });
  await col("journal").createIndex({ room: 1, seq: 1 });
}

// ------------------------------------------------------------------ carregar
async function load(roomId) {
  roomId = String(roomId);
  if (states.has(roomId)) return touch(states.get(roomId));
  if (loading.has(roomId)) return loading.get(roomId);
  const p = (async () => {
    const meta = await col("rooms").findOne({ _id: roomId });
    if (!meta) return null;
    const st = {
      id: roomId, meta, trees: generateTrees(meta).trees,
      mods: new Map(), fields: {}, pending: [], dirtyChunks: new Set(), dirtyFields: new Set(),
      journalOps: 0, journaledSeq: 0, lastUse: Date.now(), flushing: null,
    };
    for (const f of WORLD_FIELDS) if (meta[f] !== undefined) st.fields[f] = meta[f];
    for await (const c of col("chunks").find({ room: roomId })) {
      for (const [k, v] of Object.entries(c.b || {})) st.mods.set(k, v);
    }
    let replayed = 0;
    for await (const j of col("journal").find({ room: roomId }).sort({ seq: 1 })) {
      for (const op of j.ops || []) applyOp(st, op, false);
      replayed += (j.ops || []).length;
      st.journaledSeq = Math.max(st.journaledSeq, j.seq);
      st.journalOps += (j.ops || []).length;
    }
    if (replayed) {
      console.log(`[mundo] ${roomId}: ${replayed} mudanças reaplicadas do diário`);
      // o que veio do diário ainda não está nas chunks
      for (const k of st.mods.keys()) st.dirtyChunks.add(chunkOf(k).join("|"));
      for (const f of Object.keys(st.fields)) st.dirtyFields.add(f);
    }
    states.set(roomId, st);
    return st;
  })();
  loading.set(roomId, p);
  try {
    return await p;
  } finally {
    loading.delete(roomId);
  }
}

function touch(st) {
  if (st) st.lastUse = Date.now();
  return st;
}

// ------------------------------------------------------------------ mudar
function applyOp(st, op, record) {
  const [kind, k, value] = op;
  if (kind === "b") {
    const [x, y, z] = String(k).split(",").map(Number);
    if (![x, y, z].every(Number.isFinite)) return;
    const v = value === undefined ? null : value;
    const base = baseBlock(st.meta, st.trees, x, y, z) || null;
    const same = canonical(v) === base && !(v && typeof v === "object" && v.orientation);
    if (same) st.mods.delete(k);        // voltou ao original: não ocupa espaço
    else st.mods.set(k, v);
    st.dirtyChunks.add(chunkOf(k).join("|"));
  } else if (kind === "f") {
    if (value === null || value === undefined) delete st.fields[k];
    else st.fields[k] = value;
    st.dirtyFields.add(k);
  } else {
    return;
  }
  if (record) st.pending.push(op);
}

async function setBlock(roomId, k, value) {
  const st = await load(roomId);
  if (!st) return false;
  applyOp(touch(st), ["b", String(k), value === undefined ? null : value], true);
  return true;
}

// versão síncrona para quem já tem a sala aberta (tempo real)
function setBlockLoaded(roomId, k, value) {
  const st = states.get(String(roomId));
  if (!st) return false;
  applyOp(touch(st), ["b", String(k), value === undefined ? null : value], true);
  return true;
}

async function setFields(roomId, fields) {
  const st = await load(roomId);
  if (!st) return false;
  const direct = {};
  for (const [k, v] of Object.entries(fields || {})) {
    if (k === "modifications") {
      // substitui todas as alterações (migração antiga do cliente)
      for (const old of [...st.mods.keys()]) applyOp(st, ["b", old, null], true);
      for (const [mk, mv] of Object.entries(v || {})) applyOp(st, ["b", mk, mv], true);
    } else if (WORLD_FIELDS.has(k)) {
      applyOp(st, ["f", k, v], true);
    } else if (!k.startsWith("$") && !k.includes(".") && k !== "_id") {
      direct[k] = v;
      st.meta[k] = v;
    }
  }
  touch(st);
  if (Object.keys(direct).length) await col("rooms").updateOne({ _id: st.id }, { $set: direct });
  return true;
}

async function roomData(roomId) {
  const st = await load(roomId);
  if (!st) return null;
  const meta = (await col("rooms").findOne({ _id: st.id })) || st.meta;   // nome/dono frescos
  const out = { ...meta };
  delete out._id;
  for (const f of WORLD_FIELDS) delete out[f];
  Object.assign(out, st.fields);
  out.modifications = Object.fromEntries(st.mods);
  return out;
}

// ------------------------------------------------------------------ diário
async function flushJournal(st) {
  while (st.flushing) await st.flushing;    // um lote de cada vez, por ordem
  if (!st.pending.length) return;
  const ops = st.pending;
  st.pending = [];
  const seq = nextSeq();
  st.flushing = col("journal").insertOne({ room: st.id, seq, at: new Date(), ops }, MAJORITY)
    .then(() => {
      st.journaledSeq = Math.max(st.journaledSeq, seq);
      st.journalOps += ops.length;
    })
    .catch((e) => {
      console.warn(`[mundo] ${st.id}: diário falhou (${e.message}); tento de novo`);
      st.pending = ops.concat(st.pending);
    })
    .finally(() => { st.flushing = null; });
  await st.flushing;
}

// ------------------------------------------------------------------ fotos
async function snapshot(st) {
  if (st.snapshotting) return st.snapshotting;
  st.snapshotting = doSnapshot(st).finally(() => { st.snapshotting = null; });
  return st.snapshotting;
}

async function doSnapshot(st) {
  await flushJournal(st);
  if (st.pending.length) return;            // diário ainda por gravar: espera
  if (!st.dirtyChunks.size && !st.dirtyFields.size) return;
  const upto = st.journaledSeq;
  const chunks = [...st.dirtyChunks];
  const fields = [...st.dirtyFields];
  st.dirtyChunks.clear();
  st.dirtyFields.clear();
  try {
    const byChunk = new Map(chunks.map((c) => [c, {}]));
    for (const [k, v] of st.mods) {
      const c = chunkOf(k).join("|");
      if (byChunk.has(c)) byChunk.get(c)[k] = v;
    }
    const writes = [];
    for (const [c, b] of byChunk) {
      const [cx, cz] = c.split("|").map(Number);
      const _id = `${st.id}|${cx}|${cz}`;
      if (Object.keys(b).length) {
        writes.push({ replaceOne: { filter: { _id }, upsert: true,
          replacement: { _id, room: st.id, cx, cz, b, seq: upto, updated: new Date() } } });
      } else {
        writes.push({ deleteOne: { filter: { _id } } });
      }
    }
    if (writes.length) await col("chunks").bulkWrite(writes, { ordered: false, ...MAJORITY });
    if (fields.length) {
      const set = {}, unset = {};
      for (const f of fields) {
        if (st.fields[f] === undefined) unset[f] = "";
        else set[f] = st.fields[f];
      }
      const upd = {};
      if (Object.keys(set).length) upd.$set = set;
      if (Object.keys(unset).length) upd.$unset = unset;
      await col("rooms").updateOne({ _id: st.id }, upd, MAJORITY);
    }
    // o diário até "upto" já está nas fotos
    if (upto) await col("journal").deleteMany({ room: st.id, seq: { $lte: upto } });
    st.journalOps = 0;
  } catch (e) {
    console.warn(`[mundo] ${st.id}: foto falhou (${e.message}); fica para a próxima`);
    for (const c of chunks) st.dirtyChunks.add(c);
    for (const f of fields) st.dirtyFields.add(f);
  }
}

async function snapshotAll() {
  for (const st of [...states.values()]) await snapshot(st);
}

async function flushAll() {
  for (const st of [...states.values()]) await flushJournal(st);
}

// ------------------------------------------------------------------ reset
async function reset(roomId) {
  const st = await load(roomId);
  if (!st) return;
  await flushJournal(st);
  st.mods.clear();
  st.fields = {};
  st.pending = [];
  st.dirtyChunks.clear();
  st.dirtyFields.clear();
  await col("chunks").deleteMany({ room: st.id });
  await col("journal").deleteMany({ room: st.id });
  await col("room_water").deleteOne({ _id: st.id });     // a água vai junto
  await col("rooms").updateOne({ _id: st.id },
    { $unset: Object.fromEntries([...WORLD_FIELDS].map((f) => [f, ""])) });
}

// ------------------------------------------------------------------ laços
function start(isActive) {
  setInterval(() => {
    for (const st of states.values()) {
      flushJournal(st).catch(() => {});
    }
  }, JOURNAL_MS);
  setInterval(async () => {
    const now = Date.now();
    for (const st of [...states.values()]) {
      try {
        await snapshot(st);
        if (!isActive(st.id) && now - st.lastUse > IDLE_UNLOAD_MS
            && !st.pending.length && !st.dirtyChunks.size && !st.dirtyFields.size) {
          states.delete(st.id);              // sala parada: sai da memória
        }
      } catch (e) {
        console.warn("[mundo]", e.message);
      }
    }
  }, SNAPSHOT_MS);
  // diário grande numa sala: foto mais cedo
  setInterval(() => {
    for (const st of states.values()) {
      if (st.journalOps > SNAPSHOT_OPS) snapshot(st).catch(() => {});
    }
  }, 5000);
}

// ------------------------------------------------------------------ migração
// Formato antigo: room_blocks (um documento por bloco) -> chunks. Só corre se
// a coleção antiga existir; no fim ela é renomeada (cópia de segurança).
async function migrateOldBlocks() {
  const names = (await db.db.listCollections({ name: "room_blocks" }).toArray()).map((c) => c.name);
  if (!names.length) return 0;
  const byRoom = new Map();
  let n = 0;
  for await (const b of col("room_blocks").find({})) {
    if (!byRoom.has(b.room)) byRoom.set(b.room, []);
    byRoom.get(b.room).push([b.key, b.value]);
    n++;
  }
  for (const [roomId, entries] of byRoom) {
    const st = await load(roomId);
    if (!st) continue;
    for (const [k, v] of entries) applyOp(st, ["b", k, v], false);
    for (const k of st.mods.keys()) st.dirtyChunks.add(chunkOf(k).join("|"));
    await snapshot(st);
  }
  const backup = `room_blocks_antigo_${new Date().toISOString().slice(0, 10).replace(/-/g, "")}`;
  await col("room_blocks").rename(backup, { dropTarget: true });
  console.log(`[mundo] migração: ${n} blocos de room_blocks -> chunks (cópia em ${backup})`);
  return n;
}

module.exports = {
  CHUNK, JOURNAL_MS, SNAPSHOT_MS, ensureIndexes, load, setBlock, setBlockLoaded, setFields,
  roomData, flushAll, snapshot, snapshotAll, reset, start, migrateOldBlocks, states, cellKey,
};
