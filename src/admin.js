// Administração (moderadores): rotas /api/admin/* usadas pelo programa
// "Painel de administração" (projeto admin/, em Python).
//
// Quem pode: jogadores com papel "moderador" ou "admin" no registo (o dono do
// servidor dá o papel com  node tools/moderador.js <usuario>). O login do
// painel é separado do jogo (outra sessão, não marca a conta como online).
//
// O que se faz:
//   salas      ver todas; criar salas públicas e dungeons; fechar/reabrir
//              (só públicas), interditar/liberar (qualquer uma, com motivo),
//              apagar (só as criadas pelo painel, fechadas e vazias).
//   mobs       por sala: desligar o spawn, mudar o máximo/recarga de cada
//              tipo (ou pôr um tipo que não nascia ali), matar, fazer nascer.
//   balanceam. item_balancing.json: editado no painel, guardado no MongoDB
//              (vale mais que o ficheiro), aplicado na hora no servidor e nos
//              jogos ligados. "Restaurar" volta ao ficheiro.
//   registo    tudo o que um moderador faz fica gravado (admin_log).
const crypto = require("crypto");
const db = require("./db");
const rooms = require("./rooms");
const physics = require("./physics/bridge");
const { verifyPassword } = require("./passwords");

const ROLES = new Set(["moderador", "admin"]);
const SESSION_HOURS = 12;
const PROTECTED = new Set(["room_publica"]);   // a praça (todos voltam para lá) não fecha
const ESTILOS_DEFAULT = ["desarmado", "1h", "dual", "espada_escudo", "escudo", "2h", "polearm"];

const col = (name) => db.db.collection(name);

// ---------------------------------------------------------------- estado em memória
const blockedRooms = new Map();        // roomId -> {fechada, interditada, motivo}
const overrides = {};                  // "item_balancing.json" -> dados (do MongoDB)

function blocked(roomId) {
  return blockedRooms.get(String(roomId)) || null;
}

function setBlocked(roomId, doc) {
  if (doc && (doc.fechada || doc.interditada)) {
    blockedRooms.set(String(roomId), { fechada: !!doc.fechada, interditada: !!doc.interditada,
                                       motivo: (doc.interditada && doc.interditada.motivo) || "" });
  } else {
    blockedRooms.delete(String(roomId));
  }
}

// Ao arrancar: salas bloqueadas e balanceamento do painel.
async function init() {
  const docs = await col("rooms").find({ $or: [{ fechada: true }, { interditada: { $exists: true, $ne: null } }] },
    { projection: { fechada: 1, interditada: 1 } }).toArray();
  for (const d of docs) setBlocked(d._id, d);
  const ov = await col("config").findOne({ _id: "item_balancing.json" });
  if (ov && ov.dados) overrides["item_balancing.json"] = ov.dados;
  require("./economy").setOverrides(overrides);
  pushConfig();
  if (Object.keys(overrides).length) console.log("[admin] balanceamento do painel em uso");
}

// O Python (economia, perfil de combate) usa o mesmo balanceamento.
function pushConfig() {
  physics.send({ op: "eco_config", files: { ...overrides } });
}

// ---------------------------------------------------------------- sessões
const failures = new Map();            // usuario -> {n, until}

async function login(usuario, senha) {
  const key = String(usuario || "").toLowerCase();
  const f = failures.get(key);
  if (f && f.until > Date.now() && f.n >= 5) {
    return { status: 429, body: { erro: "muitas tentativas; espere 1 minuto" } };
  }
  const p = usuario ? await db.findPlayerByUser(usuario) : null;
  if (!p || !verifyPassword(String(senha || ""), p.senha_hash) || !ROLES.has(p.papel)) {
    const n = (f && f.until > Date.now() ? f.n : 0) + 1;
    failures.set(key, { n, until: Date.now() + 60 * 1000 });
    return { status: 401, body: { erro: "usuário/senha inválidos ou sem permissão de moderador" } };
  }
  failures.delete(key);
  const token = "adm_" + crypto.randomBytes(24).toString("hex");
  await col("admin_sessions").insertOne({ _id: token, player: p._id, criada: new Date(),
                                           expira: new Date(Date.now() + SESSION_HOURS * 3600e3) });
  await log(p, "login", {});
  return { status: 200, body: { token, usuario: p.usuario, nome: p.nome || p.usuario, papel: p.papel } };
}

async function adminAuth(req, res, next) {
  try {
    const h = req.headers.authorization || "";
    const token = h.startsWith("Bearer ") ? h.slice(7) : "";
    const s = token.startsWith("adm_") ? await col("admin_sessions").findOne({ _id: token }) : null;
    if (!s || new Date(s.expira) < new Date()) return res.status(401).json({ erro: "sessão de moderador inválida" });
    const p = await db.getPlayer(s.player);
    if (!p || !ROLES.has(p.papel)) return res.status(403).json({ erro: "sem permissão de moderador" });
    req.mod = p;
    req.modToken = token;
    next();
  } catch (e) {
    next(e);
  }
}

async function log(mod, acao, info) {
  try {
    await col("admin_log").insertOne({ quando: new Date(), por: mod.usuario || String(mod._id),
                                        acao, info });
  } catch (e) {
    console.warn("[admin] registo:", e.message);
  }
  console.log(`[admin] ${mod.usuario}: ${acao} ${JSON.stringify(info).slice(0, 200)}`);
}

// ---------------------------------------------------------------- salas
function live(roomId) { return rooms.get(String(roomId)); }

function onlineIn(roomId) {
  const rt = require("./realtime/server");
  const out = [];
  for (const i of rt.peers.values()) if (i.roomId === String(roomId)) out.push(i.nome);
  return out;
}

async function listRooms() {
  const docs = await col("rooms").find({}, { projection: {
    nome: 1, owner: 1, publica: 1, nivel: 1, geracao: 1, fechada: 1, interditada: 1, criada_por: 1,
    size_x: 1, size_y: 1, size_z: 1, seed: 1, mobs_admin: 1 } }).toArray();
  const owners = [...new Set(docs.map((d) => d.owner).filter(Boolean))];
  const players = await col("players").find({ _id: { $in: owners } },
    { projection: { nome: 1, usuario: 1 } }).toArray();
  const byId = new Map(players.map((p) => [p._id, p]));
  return docs.map((d) => {
    const r = live(d._id);
    const o = d.owner ? byId.get(d.owner) : null;
    return {
      id: d._id, nome: d.nome || null,
      tipo: d.publica ? (Number(d.nivel || 1) >= 3 ? "santuario" : Number(d.nivel || 1) >= 2 ? "dungeon" : "publica") : "jogador",
      nivel: d.publica ? Math.max(1, Number(d.nivel || 1) | 0) : 0,
      dono: o ? (o.nome || o.usuario) : (d.publica ? "todos" : "?"),
      tamanho: [d.size_x, d.size_y, d.size_z], seed: d.seed,
      fechada: !!d.fechada, interditada: d.interditada || null,
      criada_por: d.criada_por || null, protegida: PROTECTED.has(d._id),
      ativa: !!r, jogadores: onlineIn(d._id), mobs: r ? r.mobs.mobs.size : 0,
      mobs_admin: d.mobs_admin || null,
    };
  });
}

const clampInt = (v, lo, hi, def) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : def;
};

async function createRoom(mod, body) {
  const tipo = body.tipo === "dungeon" ? "dungeon" : "publica";
  const nome = String(body.nome || "").trim().replace(/\s+/g, " ").slice(0, 40);
  if (!nome) return { status: 400, body: { erro: "dê um nome à sala" } };
  const seed = clampInt(body.seed, 1, 999999, 1 + Math.floor(Math.random() * 99999));
  const id = `room_${tipo === "dungeon" ? "dg" : "pub"}_${crypto.randomBytes(3).toString("hex")}`;
  let p;
  if (tipo === "dungeon") {
    const base = db.DUNGEON_ROOMS[0];
    p = { ...JSON.parse(JSON.stringify(base)), id, nome, seed,
          nivel: clampInt(body.nivel, 2, 20, 2),
          size_x: clampInt(body.size_x, 60, 300, base.size_x),
          size_z: clampInt(body.size_z, 60, 300, base.size_z) };
  } else {
    p = { ...db.PUBLIC_ROOM, id, nome, seed, nivel: 1,
          size_x: clampInt(body.size_x, 40, 300, 120), size_z: clampInt(body.size_z, 40, 300, 120) };
  }
  p.criada_por = mod.usuario;
  const made = await db.ensureRoom(p, tipo === "dungeon" ? (p.minoculus_y || 1) : db.GROUND_Y + 1);
  if (!made) return { status: 409, body: { erro: "já existe uma sala com esse id" } };
  let baus = 0;
  if (tipo === "dungeon") baus = await db.placeDungeonChests(p);
  await log(mod, "criar_sala", { id, tipo, nome, nivel: p.nivel, seed, baus });
  return { status: 200, body: { ok: true, id, baus } };
}

// Tira todos os jogadores de uma sala (vão para a praça / sala própria).
function kickAll(roomId, motivo) {
  const rt = require("./realtime/server");
  const r = live(roomId);
  if (!r) return 0;
  let n = 0;
  for (const peer of [...r.peers]) {
    rt.kick(peer, motivo);
    n += 1;
  }
  return n;
}

async function setClosed(mod, roomId, fechada) {
  const doc = await col("rooms").findOne({ _id: roomId }, { projection: { publica: 1, interditada: 1 } });
  if (!doc) return { status: 404, body: { erro: "sala não existe" } };
  if (!doc.publica) return { status: 400, body: { erro: "só salas públicas e dungeons fecham (use interditar)" } };
  if (PROTECTED.has(roomId) && fechada) return { status: 400, body: { erro: "a praça pública não pode fechar" } };
  await col("rooms").updateOne({ _id: roomId }, { $set: { fechada: !!fechada } });
  setBlocked(roomId, { ...doc, fechada: !!fechada });
  const n = fechada ? kickAll(roomId, "Esta sala foi fechada pelos moderadores") : 0;
  await log(mod, fechada ? "fechar_sala" : "reabrir_sala", { id: roomId, jogadores_tirados: n });
  return { status: 200, body: { ok: true, jogadores_tirados: n } };
}

async function setInterdicted(mod, roomId, on, motivo) {
  const doc = await col("rooms").findOne({ _id: roomId }, { projection: { fechada: 1 } });
  if (!doc) return { status: 404, body: { erro: "sala não existe" } };
  if (PROTECTED.has(roomId) && on) return { status: 400, body: { erro: "a praça pública não pode ser interditada" } };
  const value = on ? { motivo: String(motivo || "").slice(0, 200), por: mod.usuario, quando: new Date() } : null;
  await col("rooms").updateOne({ _id: roomId }, { $set: { interditada: value } });
  setBlocked(roomId, { ...doc, interditada: value });
  const n = on ? kickAll(roomId, `Sala interditada${value.motivo ? ": " + value.motivo : ""}`) : 0;
  await log(mod, on ? "interditar_sala" : "liberar_sala", { id: roomId, motivo: value && value.motivo, jogadores_tirados: n });
  return { status: 200, body: { ok: true, jogadores_tirados: n } };
}

async function deleteRoom(mod, roomId) {
  const doc = await col("rooms").findOne({ _id: roomId }, { projection: { criada_por: 1, fechada: 1 } });
  if (!doc) return { status: 404, body: { erro: "sala não existe" } };
  if (!doc.criada_por) return { status: 400, body: { erro: "só se apagam salas criadas pelo painel" } };
  if (!doc.fechada) return { status: 400, body: { erro: "feche a sala antes de a apagar" } };
  if (live(roomId)) return { status: 400, body: { erro: "ainda há jogadores na sala" } };
  const storage = require("./storage");
  await storage.reset(roomId);
  storage.states.delete(roomId);
  await col("rooms").deleteOne({ _id: roomId });
  blockedRooms.delete(roomId);
  await log(mod, "apagar_sala", { id: roomId });
  return { status: 200, body: { ok: true } };
}

// ---------------------------------------------------------------- mobs
function cleanMobsAdmin(body) {
  const { mobTypes } = require("./mobs/mobServer");
  const known = mobTypes();
  const out = { desligado: !!(body && body.desligado), tipos: {} };
  for (const [tipo, t] of Object.entries((body && body.tipos) || {})) {
    if (!known[tipo] || !t || typeof t !== "object") continue;
    const e = {};
    if (t.desligado) e.desligado = true;
    if (t.max !== undefined && t.max !== null && t.max !== "") e.max = clampInt(t.max, 0, 200, 0);
    if (t.recarga !== undefined && t.recarga !== null && t.recarga !== "") e.recarga = clampInt(t.recarga, 1, 3600, 30);
    if (Object.keys(e).length) out.tipos[tipo] = e;
  }
  return out;
}

async function mobsInfo(roomId) {
  const { mobTypes } = require("./mobs/mobServer");
  const doc = await col("rooms").findOne({ _id: roomId }, { projection: { publica: 1, nivel: 1, mobs_admin: 1, zona: 1 } });
  if (!doc) return null;
  const zona = doc.zona || (doc.publica ? (Number(doc.nivel || 1) >= 2 ? "dungeon" : "publica") : "privada");
  const r = live(roomId);
  const vivos = {};
  if (r) for (const m of r.mobs.mobs.values()) if (!m.dead) vivos[m.tipo] = (vivos[m.tipo] || 0) + 1;
  const tipos = Object.entries(mobTypes()).map(([tipo, cfg]) => {
    const sp = ((cfg.spawn || {})[zona]) || null;
    const efetivo = r ? r.mobs.spawnSpec(tipo) : null;
    return { tipo, nome: cfg.nome || tipo, padrao: sp, efetivo: efetivo ? { max: efetivo.max, recarga: efetivo.recarga } : null,
             vivos: vivos[tipo] || 0 };
  });
  return { zona, ativa: !!r, config: doc.mobs_admin || { desligado: false, tipos: {} }, tipos };
}

async function setMobs(mod, roomId, body) {
  const doc = await col("rooms").findOne({ _id: roomId }, { projection: { _id: 1 } });
  if (!doc) return { status: 404, body: { erro: "sala não existe" } };
  const cfg = cleanMobsAdmin(body);
  await col("rooms").updateOne({ _id: roomId }, { $set: { mobs_admin: cfg } });
  const r = live(roomId);
  if (r) {
    r.mobs.admin = cfg;
    r.mobs.filled = {};                 // "encher" vale de novo com os números novos
    r.mobs.trimToMax();
  }
  await log(mod, "mobs_config", { id: roomId, ...cfg });
  return { status: 200, body: { ok: true, config: cfg } };
}

async function killMobs(mod, roomId, tipo) {
  const r = live(roomId);
  if (!r) return { status: 400, body: { erro: "a sala não está aberta (ninguém lá dentro)" } };
  const n = r.mobs.adminKill(tipo || null);
  await log(mod, "matar_mobs", { id: roomId, tipo: tipo || "todos", n });
  return { status: 200, body: { ok: true, mortos: n } };
}

async function spawnMobs(mod, roomId, tipo, quantidade) {
  const r = live(roomId);
  if (!r) return { status: 400, body: { erro: "a sala não está aberta (ninguém lá dentro)" } };
  const n = clampInt(quantidade, 1, 50, 1);
  let ok = 0;
  let motivo = null;
  for (let i = 0; i < n; i++) {
    const res = r.mobs.spawn(String(tipo));
    if (res.result === "ok") ok += 1; else motivo = res.motivo;
  }
  await log(mod, "nascer_mobs", { id: roomId, tipo, pedidos: n, nasceram: ok });
  return { status: 200, body: { ok: true, nasceram: ok, motivo } };
}

// ---------------------------------------------------------------- balanceamento
function fileBalancing() {
  const fs = require("fs");
  const path = require("path");
  const config = require("./config");
  return JSON.parse(fs.readFileSync(path.join(config.dataDir, "item_balancing.json"), "utf8"));
}

function estilos() {
  try {
    const fs = require("fs");
    const path = require("path");
    const config = require("./config");
    const c = JSON.parse(fs.readFileSync(path.join(config.dataDir, "combate.json"), "utf8"));
    return Object.keys(c.estilos || {});
  } catch (e) {
    return ESTILOS_DEFAULT;
  }
}

function knownItems() {
  const fs = require("fs");
  const path = require("path");
  const config = require("./config");
  const out = new Set();
  for (const f of ["itens.json", "blocks.json"]) {
    try {
      const d = JSON.parse(fs.readFileSync(path.join(config.dataDir, f), "utf8"));
      for (const [k, v] of Object.entries(d)) {
        if (k.startsWith("_")) continue;
        out.add(k);
        if (v && typeof v === "object" && v.name) out.add(String(v.name));
      }
    } catch (e) { /* sem ficheiro */ }
  }
  return out;
}

const TEXT_ATTRS = new Set(["estilo"]);
const NUM_LIMIT = 1e6;

// Confere o balanceamento enviado: categorias -> itens -> atributos (números,
// "estilo" com um estilo de combate que existe).
function validateBalancing(dados) {
  if (!dados || typeof dados !== "object" || Array.isArray(dados)) return "formato inválido";
  const st = new Set(estilos());
  const known = knownItems();
  let n = 0;
  for (const [cat, items] of Object.entries(dados)) {
    if (cat.startsWith("_")) continue;
    if (!/^[\w\- ]{1,40}$/.test(cat)) return `categoria com nome inválido: ${cat}`;
    if (!items || typeof items !== "object" || Array.isArray(items)) return `categoria ${cat}: não é um objeto`;
    for (const [item, attrs] of Object.entries(items)) {
      if (known.size && !known.has(item)) return `item desconhecido: ${item} (tem de existir em itens.json)`;
      if (!attrs || typeof attrs !== "object" || Array.isArray(attrs)) return `${item}: atributos inválidos`;
      for (const [k, v] of Object.entries(attrs)) {
        if (!/^[\w]{1,40}$/.test(k)) return `${item}: atributo com nome inválido (${k})`;
        if (TEXT_ATTRS.has(k)) {
          if (!st.has(String(v))) return `${item}.${k}: estilo "${v}" não existe (${[...st].join(", ")})`;
        } else if (typeof v !== "number" || !Number.isFinite(v) || Math.abs(v) > NUM_LIMIT) {
          return `${item}.${k}: tem de ser um número (recebido ${JSON.stringify(v)})`;
        } else if (v < 0) {
          return `${item}.${k}: não pode ser negativo`;
        }
        n += 1;
      }
    }
  }
  return n ? null : "balanceamento vazio";
}

function diffBalancing(a, b) {
  const flat = (d) => {
    const out = {};
    for (const [cat, items] of Object.entries(d || {})) {
      if (!items || typeof items !== "object") continue;
      for (const [it, attrs] of Object.entries(items)) {
        for (const [k, v] of Object.entries(attrs || {})) out[`${it}.${k}`] = v;
      }
    }
    return out;
  };
  const fa = flat(a), fb = flat(b);
  const out = [];
  for (const k of new Set([...Object.keys(fa), ...Object.keys(fb)])) {
    if (JSON.stringify(fa[k]) !== JSON.stringify(fb[k])) out.push([k, fa[k] ?? null, fb[k] ?? null]);
  }
  return out;
}

function currentBalancing() {
  return overrides["item_balancing.json"] || fileBalancing();
}

async function applyBalancing(mod, dados, acao) {
  const before = currentBalancing();
  const economy = require("./economy");
  if (dados === null) {
    delete overrides["item_balancing.json"];
    await col("config").deleteOne({ _id: "item_balancing.json" });
  } else {
    overrides["item_balancing.json"] = dados;
    await col("config").updateOne({ _id: "item_balancing.json" },
      { $set: { dados, por: mod.usuario, quando: new Date() } }, { upsert: true });
  }
  economy.setOverrides(overrides);
  pushConfig();
  // os jogos ligados recebem o balanceamento novo na hora
  const rt = require("./realtime/server");
  const versao = economy.configBundle().versao;
  rt.broadcastAll({ t: "config", versao });
  const mudou = diffBalancing(before, currentBalancing());
  await log(mod, acao, { mudancas: mudou.slice(0, 200), total: mudou.length });
  return { status: 200, body: { ok: true, versao, mudancas: mudou } };
}

// ---------------------------------------------------------------- rotas
function routes(app, wrap) {
  const send = (res, r) => res.status(r.status).json(r.body);

  app.post("/api/admin/login", wrap(async (req, res) => {
    const { usuario, senha } = req.body || {};
    send(res, await login(usuario, senha));
  }));
  app.post("/api/admin/logout", adminAuth, wrap(async (req, res) => {
    await col("admin_sessions").deleteOne({ _id: req.modToken });
    res.json({ ok: true });
  }));
  app.get("/api/admin/me", adminAuth, (req, res) =>
    res.json({ usuario: req.mod.usuario, nome: req.mod.nome, papel: req.mod.papel }));

  app.get("/api/admin/salas", adminAuth, wrap(async (req, res) => res.json(await listRooms())));
  app.post("/api/admin/salas", adminAuth, wrap(async (req, res) => send(res, await createRoom(req.mod, req.body || {}))));
  app.post("/api/admin/salas/:id/fechar", adminAuth, wrap(async (req, res) =>
    send(res, await setClosed(req.mod, req.params.id, (req.body || {}).fechada !== false))));
  app.post("/api/admin/salas/:id/interditar", adminAuth, wrap(async (req, res) =>
    send(res, await setInterdicted(req.mod, req.params.id, (req.body || {}).interditada !== false,
                                   (req.body || {}).motivo))));
  app.delete("/api/admin/salas/:id", adminAuth, wrap(async (req, res) => send(res, await deleteRoom(req.mod, req.params.id))));

  app.get("/api/admin/salas/:id/mobs", adminAuth, wrap(async (req, res) => {
    const info = await mobsInfo(req.params.id);
    if (!info) return res.status(404).json({ erro: "sala não existe" });
    res.json(info);
  }));
  app.put("/api/admin/salas/:id/mobs", adminAuth, wrap(async (req, res) =>
    send(res, await setMobs(req.mod, req.params.id, req.body || {}))));
  app.post("/api/admin/salas/:id/mobs/matar", adminAuth, wrap(async (req, res) =>
    send(res, await killMobs(req.mod, req.params.id, (req.body || {}).tipo))));
  app.post("/api/admin/salas/:id/mobs/nascer", adminAuth, wrap(async (req, res) =>
    send(res, await spawnMobs(req.mod, req.params.id, (req.body || {}).tipo, (req.body || {}).quantidade))));

  app.get("/api/admin/balanceamento", adminAuth, wrap(async (req, res) => {
    res.json({ dados: currentBalancing(), origem: overrides["item_balancing.json"] ? "painel" : "ficheiro",
               estilos: estilos(), itens: [...knownItems()].sort() });
  }));
  app.put("/api/admin/balanceamento", adminAuth, wrap(async (req, res) => {
    const dados = (req.body || {}).dados;
    const erro = validateBalancing(dados);
    if (erro) return res.status(400).json({ erro });
    send(res, await applyBalancing(req.mod, dados, "balanceamento"));
  }));
  app.delete("/api/admin/balanceamento", adminAuth, wrap(async (req, res) =>
    send(res, await applyBalancing(req.mod, null, "balanceamento_restaurado"))));

  app.get("/api/admin/registo", adminAuth, wrap(async (req, res) => {
    const n = clampInt(req.query && req.query.n, 1, 500, 200);
    const docs = await col("admin_log").find({}).sort({ quando: -1 }).limit(n).toArray();
    res.json(docs.map((d) => ({ quando: d.quando, por: d.por, acao: d.acao, info: d.info })));
  }));
}

module.exports = { init, routes, blocked, pushConfig, overrides, validateBalancing, diffBalancing,
                   cleanMobsAdmin, ROLES };
