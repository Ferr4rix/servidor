// API HTTP (Express): contas, mundos e saves. O tempo real vai pelo ENet.
const express = require("express");
const db = require("../db");
const rooms = require("../rooms");
const storage = require("../storage");
const physics = require("../physics/bridge");
const { hashPassword, verifyPassword, isHashed } = require("../passwords");
const access = require("../access");
const presence = require("../presence");
const economy = require("../economy");
const admin = require("../admin");

const KEY_RE = /^-?\d+,-?\d+,-?\d+$/;
const SERVER_OWNED = new Set(["mobs", "seed", "size_x", "size_y", "size_z", "_id",
                              "nome", "owner", "publica", "praca_raio", "nivel",
                              "geracao", "material", "revestimento", "camadas", "labirinto",
                              "escadas", "versao", "lagos", "lagos_agua", "zona", "ceu", "ambiente", "plantas"]);
// Do registo do jogador, o jogo só pode gravar isto (o resto é do servidor:
// inventário, almas, status base, itens iniciais, acesso aos mundos...).
const PLAYER_WRITABLE = new Set([]);   // last_position: ver PUT /api/players/me
// Mundos dos jogadores: 40x40 blocos (a sala pública tem 200x200).
const PLAYER_WORLD_SIZE = 40;

function wrap(fn) {
  return (req, res) => fn(req, res).catch((e) => {
    console.error("[http]", e);
    res.status(500).json({ erro: "erro interno do servidor" });
  });
}

async function auth(req, res, next) {
  try {
    const h = req.headers.authorization || "";
    const token = h.startsWith("Bearer ") ? h.slice(7) : null;
    const pid = await db.resolveSession(token);
    if (!pid) return res.status(401).json({ erro: "sessão inválida ou expirada" });
    presence.touch(pid, token);           // qualquer pedido = está vivo
    req.playerId = pid;
    req.token = token;
    req.player = await db.getPlayer(pid);
    if (!req.player) return res.status(401).json({ erro: "jogador não existe" });
    next();
  } catch (e) {
    next(e);
  }
}

function canAccess(player, roomId) {
  if (admin.blocked(roomId)) return false;     // fechada / interditada pelos moderadores
  if ((player.link_room_properties || []).map(String).includes(String(roomId))) return true;
  // a sala onde estava ao sair (gravada pelo servidor ao entrar): volta lá ao logar
  if (player.ultima_sala && String(player.ultima_sala) === String(roomId)) return true;
  return access.has(player._id, roomId);       // dungeon: passe de quem entrou pela praça
}

// Sala (e nível) onde o jogador está agora, pelo tempo real (ENet).
function currentLevel(pid) {
  const rt = require("../realtime/server");
  const roomId = rt.roomOfPlayer(pid);
  const live = roomId ? rooms.get(roomId) : null;
  return { roomId, level: live ? access.levelOf(live.world.meta) : 0 };
}

async function newWorld(player, nome) {
  const id = await db.createRoom({
    nome: String(nome || "Novo mundo").slice(0, 32),
    seed: 1 + Math.floor(Math.random() * 99999),
    size_x: PLAYER_WORLD_SIZE, size_y: 20, size_z: PLAYER_WORLD_SIZE,
    layers: 3, owner: player._id,
  });
  const links = [...(player.link_room_properties || []), id];
  await db.savePlayer(player._id, { link_room_properties: links });
  player.link_room_properties = links;
  return id;
}

// Garante a sala própria (cria uma se o jogador não tiver) e devolve o id.
async function ensureHome(player) {
  let home = await db.findOwnRoom(player);
  if (!home) home = await newWorld(player, "Mundo 1");
  return String(home);
}

// O que o login devolve do jogador: os dados mais a sala própria e a última
// sala (onde ele volta ao entrar; a última sala é gravada pelo servidor).
async function loginData(player) {
  // o inventário e as almas vêm do livro do servidor (com os itens iniciais)
  const eco = await economy.load(player._id, { grant: true });
  player = await db.getPlayer(player._id);
  const data = db.publicPlayer(player);
  delete data.economia;
  data.propertie_inventory = eco.inventory;
  data.almas = eco.almas;
  data.properties_status = eco.properties_status;
  data.sala_propria = await ensureHome(player);
  if (player.ultima_sala && !admin.blocked(player.ultima_sala)) data.ultima_sala = String(player.ultima_sala);
  return data;
}

function createApp() {
  const app = express();
  app.use(express.json({ limit: "20mb" }));

  app.get("/api/health", (req, res) => res.json({ ok: true, salas_ativas: rooms.active.size }));

  // Painel de administração (moderadores): ver admin.js
  admin.routes(app, wrap);

  app.post("/api/login", wrap(async (req, res) => {
    const { usuario, senha } = req.body || {};
    const p = usuario ? await db.findPlayerByUser(usuario) : null;
    if (!p || !verifyPassword(String(senha || ""), p.senha_hash)) {
      return res.status(401).json({ erro: "Usuário ou senha inválidos." });
    }
    // Uma conta só pode estar aberta num lugar: se já está online, recusa.
    // (Ver e marcar sem "await" no meio: dois logins ao mesmo tempo não passam.)
    if (presence.isOnline(p._id)) {
      return res.status(409).json({
        erro: "Esta conta já está online. Saia do jogo no outro computador " +
              `(se ele fechou sem sair, espere ${Math.round(presence.ONLINE_SECONDS)} s) e tente de novo.`,
      });
    }
    presence.touch(p._id);
    if (!isHashed(p.senha_hash)) await db.setPasswordHash(p._id, hashPassword(String(senha)));
    const token = await db.createSession(p._id);
    await db.deleteSessionsOf(p._id, token);   // tokens antigos deixam de valer
    presence.touch(p._id, token);
    res.json({ player_id: p._id, token, data: await loginData(await db.getPlayer(p._id)) });
  }));

  app.post("/api/register", wrap(async (req, res) => {
    const { usuario, senha, nome } = req.body || {};
    if (!usuario || !senha || String(senha).length < 4) {
      return res.status(400).json({ erro: "usuário e senha (4+ caracteres) são obrigatórios" });
    }
    if (await db.findPlayerByUser(usuario)) return res.status(409).json({ erro: "usuário já existe" });
    if (/\s/.test(String(usuario)) || String(usuario).length > 32) {
      return res.status(400).json({ erro: "usuário inválido (sem espaços, até 32 caracteres)" });
    }
    const id = await db.createPlayer(String(usuario), hashPassword(String(senha)),
      String(nome || usuario).trim().slice(0, 24));
    const player = await db.getPlayer(id);
    await newWorld(player, "Mundo 1");
    const token = await db.createSession(id);
    presence.touch(id, token);
    res.json({ player_id: id, token, data: await loginData(await db.getPlayer(id)) });
  }));

  // Sinal de vida do jogo (a cada 10 s): mantém a conta "online" mesmo no
  // menu dos mundos, sem ligação ao tempo real. O auth já marca a presença.
  app.post("/api/presence", auth, (req, res) => res.json({ ok: true }));

  // Sair: a conta fica livre na hora e o token deixa de valer.
  app.post("/api/logout", auth, wrap(async (req, res) => {
    presence.release(req.playerId, req.token);
    await db.deleteSession(req.token);
    res.json({ ok: true });
  }));

  // Configurações do jogo (itens, receitas, blocos, mobs, combate): o jogo
  // usa estas e não as cópias locais.
  app.get("/api/config", auth, (req, res) => res.json(economy.configBundle()));

  app.put("/api/players/me", auth, wrap(async (req, res) => {
    const body = (req.body || {}).data || {};
    const data = {};
    for (const k of PLAYER_WRITABLE) if (body[k] && typeof body[k] === "object") data[k] = body[k];
    // status: só a vida atual (o resto é igual para todos, economia.json)
    if (body.properties_status && typeof body.properties_status === "object") {
      data.properties_status = economy.cleanStatus(req.player.properties_status,
                                                   body.properties_status, req.player.almas);
    }
    // posição: a da sala onde está é a que o servidor conferiu; das outras
    // salas só se pode apagar (voltar ao spawn), nunca inventar uma
    if (body.last_position && typeof body.last_position === "object") {
      const stored = { ...(req.player.last_position || {}) };
      const live = require("../realtime/server").livePosition(req.playerId);
      const out = {};
      for (const k of Object.keys(body.last_position)) {
        if (live && live.room === k && live.pos) {
          out[k] = live.pos.map((v) => v.toFixed(2)).join(", ");
        } else if (stored[k] !== undefined) {
          out[k] = stored[k];
        }
      }
      data.last_position = out;
    }
    // inventário e almas: chegam pelo tempo real ({t:"inv"}) e são conferidos
    await db.savePlayer(req.playerId, data);
    res.json({ ok: true });
  }));

  // Mundos: os do jogador primeiro, depois os dos outros (todos públicos por
  // agora), cada um com o dono e quantos jogadores estão lá agora.
  app.get("/api/worlds", auth, wrap(async (req, res) => {
    const mine = (req.player.link_room_properties || []).map(String);
    await db.ensurePublicRoom();                 // garante a sala pública
    const here = currentLevel(req.playerId);
    // Dungeons (nível 2+) só aparecem para quem está numa sala pública do
    // nível anterior (ou já está nela).
    const all = (await db.listAllRooms()).filter((r) => !r.publica
      || access.canEnterLevel(r.nivel || 1, here.level, here.roomId, r.id));
    const out = all.map((r) => ({
      id: r.id,
      nome: r.nome || `Mundo de ${r.dono}`,
      dono: r.dono,
      meu: !r.publica && (r.owner_id === req.playerId || (mine.includes(r.id) && !r.owner_id)),
      publica: !!r.publica,
      nivel: r.publica ? (r.nivel || 1) : 0,
      acesso: mine.includes(r.id),
      online: rooms.get(r.id) ? rooms.get(r.id).peers.size : 0,
    }));
    out.sort((a, b) => (b.publica - a.publica) || (b.meu - a.meu) || (b.online - a.online)
      || String(a.nome).localeCompare(b.nome));
    res.json(out);
  }));

  // Entrar no mundo de outro jogador: dá acesso (fica na lista do jogador).
  app.post("/api/worlds/:id/join", auth, wrap(async (req, res) => {
    const room = await db.getRoom(req.params.id, { withBlocks: false });
    if (!room) return res.status(404).json({ erro: "esse mundo não existe" });
    const blk = admin.blocked(req.params.id);
    if (blk) {
      return res.status(403).json({ erro: blk.interditada
        ? `sala interditada pelos moderadores${blk.motivo ? ": " + blk.motivo : ""}` : "sala fechada" });
    }
    const lvl = access.levelOf(room);
    if (lvl >= 2) {
      // Dungeon: só a partir de uma sala pública do nível anterior. Não entra
      // na lista do jogador: dá um passe para esta viagem.
      const here = currentLevel(req.playerId);
      if (!access.canEnterLevel(lvl, here.level, here.roomId, req.params.id)) {
        return res.status(403).json({ erro: `só se entra pelo minoculus de uma sala pública de nível ${lvl - 1}` });
      }
      access.grant(req.playerId, req.params.id);
      return res.json({ ok: true, id: req.params.id, temporario: true });
    }
    if (!room.owner && !room.publica) {
      // Mundo antigo sem dono gravado: o dono é quem já tinha acesso antes.
      const prev = await db.db.collection("players").findOne(
        { link_room_properties: req.params.id, _id: { $ne: req.playerId } }, { projection: { _id: 1 } });
      await db.setRoomFields(req.params.id, { owner: prev ? prev._id : req.playerId });
    }
    const links = (req.player.link_room_properties || []).map(String);
    if (!links.includes(req.params.id)) {
      links.push(req.params.id);
      await db.savePlayer(req.playerId, { link_room_properties: links });
    }
    res.json({ ok: true, id: req.params.id });
  }));

  // Renomear um mundo: só o dono (a sala pública não).
  app.patch("/api/worlds/:id", auth, wrap(async (req, res) => {
    const nome = String((req.body || {}).nome || "").trim().replace(/\s+/g, " ").slice(0, 32);
    if (!nome) return res.status(400).json({ erro: "nome vazio" });
    const room = await db.getRoom(req.params.id, { withBlocks: false });
    if (!room) return res.status(404).json({ erro: "esse mundo não existe" });
    if (room.publica || String(room.owner) !== String(req.playerId)) {
      return res.status(403).json({ erro: "só o dono pode mudar o nome do mundo" });
    }
    await db.setRoomFields(req.params.id, { nome });
    res.json({ ok: true, nome });
  }));

  app.post("/api/worlds", auth, wrap(async (req, res) => {
    const id = await newWorld(req.player, (req.body || {}).nome);
    res.json({ id });
  }));

  app.get("/api/rooms/:id", auth, wrap(async (req, res) => {
    if (!canAccess(req.player, req.params.id)) return res.status(403).json({ erro: "sem acesso a esta sala" });
    const room = await storage.roomData(req.params.id);      // memória: sempre o mais recente
    if (!room) return res.status(404).json({ erro: `Sala ${req.params.id} não existe.` });
    delete room.mobs;
    res.json(room);
  }));

  // Campos da sala: quem os decide é o servidor (física: ligações, cola;
  // baús e almas: pedidos "chest"/"bank" do tempo real, conferidos com o
  // livro). O jogo já não grava campos de sala; o pedido é aceite e ignorado.
  app.patch("/api/rooms/:id", auth, wrap(async (req, res) => {
    if (!canAccess(req.player, req.params.id)) return res.status(403).json({ erro: "sem acesso a esta sala" });
    res.json({ ok: true, ignorado: Object.keys((req.body || {}).fields || {}) });
  }));

  // Pôr/tirar blocos é só pelo tempo real (pagos com o inventário).
  app.post("/api/rooms/:id/blocks", auth, (req, res) =>
    res.status(403).json({ erro: "blocos só pelo jogo (tempo real)" }));

  // Baús portáteis (no inventário / na mão): conteúdo por id.
  const CID = /^cnt-[a-f0-9]{8,32}$/;
  app.get("/api/containers/:id", auth, wrap(async (req, res) => {
    if (!CID.test(req.params.id)) return res.status(400).json({ erro: "id inválido" });
    const doc = await db.db.collection("containers").findOne({ _id: req.params.id });
    res.json({ slots: (doc && doc.slots) || [] });
  }));
  // Gravar/apagar conteúdo: só o servidor (pedido "icont" do tempo real e
  // pôr/tirar o baú do mundo).
  app.put("/api/containers/:id", auth, (req, res) =>
    res.status(403).json({ erro: "o conteúdo dos baús é gravado pelo servidor" }));
  app.delete("/api/containers/:id", auth, (req, res) =>
    res.status(403).json({ erro: "o conteúdo dos baús é gravado pelo servidor" }));

  app.post("/api/rooms/:id/reset", auth, wrap(async (req, res) => {
    if (!canAccess(req.player, req.params.id)) return res.status(403).json({ erro: "sem acesso a esta sala" });
    const meta = await db.getRoom(req.params.id, { withBlocks: false });
    if (!meta || meta.publica || String(meta.owner) !== String(req.playerId)) {
      return res.status(403).json({ erro: "só o dono pode recomeçar o mundo" });
    }
    await storage.reset(req.params.id);
    const live = rooms.get(req.params.id);
    if (live) {
      live.world.mods.clear();
      await rooms.clearWater(req.params.id);       // a água volta ao início também
      physics.send({ op: "close", room: req.params.id });
      physics.send({ op: "open", room: req.params.id, data: await storage.roomData(req.params.id) });
    }
    res.json({ ok: true });
  }));

  app.use((err, req, res, next) => {
    console.error("[http]", err);
    res.status(500).json({ erro: "erro interno do servidor" });
  });
  return app;
}

module.exports = { createApp, canAccess, KEY_RE };
