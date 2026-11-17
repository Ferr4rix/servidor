// Tempo real (ENet/UDP): jogadores, blocos e mobs de cada sala.
//
// Mensagens (JSON em UTF-8):
//   cliente -> servidor
//     {t:"hello", token, room}                 entrar numa sala (token do login HTTP)
//     {t:"state", pos, yaw, ...}               estado do jogador (20x por segundo)
//     {t:"hit", cell, damage, dir}             golpe/encontrão num bloco (a física decide)
//     {t:"place", cell, tipo, orientation}     pôr um bloco
//     {t:"remove", cell}                       tirar um bloco (Q)
//     {t:"glue", cell, normal}                 cola numa face
//     {t:"block", key, value}                  (legado) bloco direto, passa pela física
//     {t:"attack", req, mob, damage, origin, kind}   golpe num mob
//     {t:"deflect", proj, dir}                       espada rebate um projétil
//     {t:"water", changes:[[key, nível]...]}   água mudou (simulação ou balde)
//     {t:"crank", cell, dir}                   está a girar uma manivela (torque; dir 1 / -1)
//     {t:"screw", s:[[key, [a, b]]...]}        bolsas de água dos parafusos (quem simula)
//   economia (conferido com o livro do jogador, ver economy.js):
//     {t:"inv", inv, almas}                    o inventário que o jogo gravou
//     {t:"chest", cell, entries}               fechou um baú do mundo
//     {t:"bank", cell, delta}                  guardou (+) / tirou (-) almas do minoculus
//     {t:"icont", cid, entries, size}          fechou um baú que leva no inventário
//     {t:"transmute", idx, item, stone_d}      transmutação (receitas do servidor)
//     {t:"test", kind}                         F6/F7/F8 (só no modo de testes)
//     {t:"bye"}
//   servidor -> cliente
//     {t:"welcome", player_id, room}
//     {t:"error", msg}
//     {t:"snap", time, host, players:[...], mobs:[...], projectiles:[...], events:[...]}   20x por segundo
//                                              (host = quem simula a água: menor id)
//     {t:"water", changes}                      água mudada por outro jogador
//     {t:"water_state", cells}                  a água toda da sala (ao entrar)
//     {t:"crank", cell, dir, by}                alguém está a girar uma manivela
//     {t:"screw", s} / {t:"screw_state", s}     bolsas dos parafusos (mudanças / todas ao entrar)
//     {t:"blocks", changes:[[key, value]...]}  o que a física do servidor mudou (todos)
//     {t:"fields", fields}                      baús/cola/almas mudados
//     {t:"anim", items:[[tipo, dados]...]}      quedas/tombos para animar
//     {t:"fx", items}                           partículas (golpes, quebras)
//     {t:"hit_result" | "destroyed" | "reject", ...}   só para quem pediu
//     {t:"attack_result", req, mob, result, damage}
//     {t:"player_left", id}
//     {t:"inv_fix", inv, almas}                 o servidor corrigiu o inventário
const db = require("../db");
const rooms = require("../rooms");
const storage = require("../storage");
const physics = require("../physics/bridge");
const { canAccess, KEY_RE } = require("../http/app");
const access = require("../access");
const { startHost, send } = require("./enetHost");
const { TICK, HEAD_MULT } = require("../mobs/mobServer");
const config = require("../config");
const economy = require("../economy");
const guard = require("../guard");
const { blockDefs } = require("../world/roomWorld");
const NEAR_MOBS = 32;          // blocos: mobs até aqui vão em todos os snaps
const FULL_EVERY = 5;          // a cada 5 ticks (4x/s) vão todos os que interessam
const SEND_MOBS = 80;          // blocos (na horizontal): mais longe nem se mandam (névoa a ~61)
const SEND_MOBS_Y = 24;        // blocos na vertical (dungeon: os outros andares não se veem)

// PvP: um golpe de jogador em jogador. O cliente de quem bate diz quem e onde
// (cabeça/corpo); o servidor confere distância, ritmo e dano e avisa a vítima,
// que aplica o dano com a armadura dela.
const PVP_REACH = 7 * 2.0;        // unidades do mundo (7 blocos)
const PVP_MIN_INTERVAL = 120;     // ms entre golpes do mesmo atacante
const PVP_MAX_DAMAGE = 100;

// Água: simulada nos jogos (um só por sala, o "host"); o servidor só sabe
// onde ela está (memória) para a passar a quem entra. Nunca é gravada.
const WATER_MAX_CHANGES = 4096;     // por mensagem
const WATER_MAX_CELLS = 200000;     // por sala (proteção)

const peers = new Map();       // peer -> { playerId, nome, roomId, state }

function others(room, except) {
  const out = [];
  for (const peer of room.peers) {
    const info = peers.get(peer);
    if (!info || peer === except || !info.state) continue;
    out.push({ id: info.playerId, nome: info.nome, ...info.state });
  }
  return out;
}

function broadcast(room, obj, except = null) {
  for (const peer of room.peers) if (peer !== except) send(peer, obj);
}

async function hello(peer, msg) {
  const pid = await db.resolveSession(msg.token);
  if (!pid) return send(peer, { t: "error", msg: "sessão inválida" });
  const player = await db.getPlayer(pid);
  if (!player || !canAccess(player, msg.room)) return send(peer, { t: "error", msg: "sem acesso a esta sala" });
  await economy.ensure(pid);            // o livro do jogador (o que ele tem)
  const room = await rooms.open(String(msg.room));
  // Um jogador só pode estar ligado uma vez: a ligação antiga sai.
  for (const [p, info] of peers) {
    if (info.playerId === pid && p !== peer) {
      leave(p);
      try { p.disconnect(); } catch (e) { /* já saiu */ }
    }
  }
  peers.set(peer, { playerId: pid, nome: player.nome || player.usuario, roomId: room.id, state: null,
                    chain: Promise.resolve() });
  guard.onJoin(peers.get(peer), player, room);     // onde pode aparecer
  // dungeon: o passe vale enquanto está lá dentro (renovado ao sair, ver leave)
  if (access.levelOf(room.world.meta) >= 2) access.grant(pid, room.id, 24 * 3600 * 1000);
  room.peers.add(peer);
  // a última sala onde entrou: é para lá que volta ao logar (mesmo a dungeon)
  db.savePlayer(pid, { ultima_sala: room.id }).catch((e) => console.warn("[enet] ultima_sala:", e.message));
  send(peer, { t: "welcome", player_id: pid, room: room.id });
  if (room.water.size) send(peer, { t: "water_state", cells: [...room.water.entries()] });
  if (room.screws.size) send(peer, { t: "screw_state", s: [...room.screws.entries()] });
  // Plantas/sóis que o servidor pôs sozinho: o jogo pode ter lido a sala
  // (HTTP) antes de nascerem (ex.: os que nascem todos ao abrir a sala) e o
  // aviso "blocks" chegou antes de ele estar na sala. Reenvia-os ao entrar.
  const nat = [];
  for (const [k, v] of room.world.mods) {
    const t = v && typeof v === "object" ? v.type : v;     // gravado como {type, ...} ou texto
    if (NATURAL_BLOCKS.has(t)) nat.push([k, v]);
  }
  if (nat.length) send(peer, { t: "blocks", changes: nat });
  console.log(`[enet] ${player.nome || pid} entrou em ${room.id} (${room.peers.size} na sala)`);
}

const NATURAL_BLOCKS = new Set(["capim", "flor lunar", "berco solar", "mini sol"]);

function leave(peer) {
  const info = peers.get(peer);
  peers.delete(peer);
  if (!info) return;
  // saiu do tempo real: continua "online" uns segundos (trocar de mundo, ou o
  // jogo ainda aberto no menu, que manda sinal de vida); se fechou, liberta-se
  require("../presence").touch(info.playerId);
  economy.flush(info.playerId).catch((e) => console.warn("[economia]", e.message));
  const room = rooms.get(info.roomId);
  if (!room) return;
  if (access.levelOf(room.world.meta) >= 2) access.grant(info.playerId, room.id);   // folga p/ voltar
  room.peers.delete(peer);
  room.mobs.removePlayer(info.playerId);
  broadcast(room, { t: "player_left", id: info.playerId });
  console.log(`[enet] ${info.nome} saiu de ${room.id}`);
  rooms.closeIfEmpty(room.id).catch((e) => console.warn("[salas]", e.message));
}

function onMessage(peer, msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.t === "hello") {
    hello(peer, msg).catch((e) => {
      console.error("[enet] hello:", e);
      send(peer, { t: "error", msg: String(e.message || e) });
    });
    return;
  }
  const info = peers.get(peer);
  if (!info) return;
  const room = rooms.get(info.roomId);
  if (!room) return;
  switch (msg.t) {
    case "state": {
      const s = { ...msg };
      delete s.t;
      if (!Array.isArray(s.pos) || s.pos.length !== 3 || !s.pos.every(Number.isFinite)) return;
      // velocidade/posição: o servidor só aceita o que o jogador consegue andar
      const f = guard.checkMove(info, s.pos, !!s.dead);
      if (f) {
        s.pos = f.pos.slice();
        if (!f.silent) send(peer, { t: "pos_fix", pos: f.pos });
      }
      info.state = s;
      room.mobs.updatePlayer(info.playerId, s);
      break;
    }
    // --- física dos blocos: o cliente pede, o Python decide ---------------
    // Tudo o que mexe em itens vai numa fila por jogador (a ordem conta: o
    // inventário gravado tem de chegar depois do bloco que ele pegou).
    case "hit":
    case "push":
    case "place":
    case "remove":
    case "glue": {
      if (!validCell(msg.cell)) return;
      const req = { op: msg.t, room: room.id, by: info.playerId, cell: msg.cell.map((v) => v | 0) };
      if (msg.t === "push") {
        // empurrar agachado: força do jogador em N (física: atrito x m x g)
        req.force = Math.min(1e5, Math.max(0, Number(msg.force) || 0));
        req.dir = validCell(msg.dir) ? msg.dir : [0, 0, 0];
      } else if (msg.t === "hit") {
        req.damage = Math.min(1e4, Math.max(0, Number(msg.damage) || 0));
        req.dir = validCell(msg.dir) ? msg.dir : [0, 0, 0];
      } else if (msg.t === "place") {
        if (typeof msg.tipo !== "string" || msg.tipo.length > 64) return;
        req.tipo = msg.tipo;
        req.orientation = typeof msg.orientation === "string" ? msg.orientation : null;
        const d = msg.data && typeof msg.data === "object" ? msg.data : {};
        req.data = {};
        if (economy.CID.test(String(d.container || ""))) req.data.container = String(d.container);
        if (Number.isFinite(Number(d.almas)) && Number(d.almas) > 0) req.data.almas = Math.floor(Number(d.almas));
      } else if (msg.t === "remove") {
        if (economy.CID.test(String(msg.cid || ""))) req.cid = String(msg.cid);
      } else if (msg.t === "glue") {
        req.normal = validCell(msg.normal) ? msg.normal : null;
      }
      queue(info, async () => {
        if (req.op === "place" && req.data.container) {
          req.contents = await economy.containerEntries(req.data.container);
        }
        if (req.op === "remove" && req.cid && await economy.containerExists(req.cid)) {
          delete req.cid;                       // id já usado: o Python recusa se houver conteúdo
        }
        physics.send(req);
      });
      break;
    }
    case "block":
      // (antigo) pôr qualquer bloco sem pagar: já não é aceite
      break;
    case "inv": {
      if (!msg.inv || typeof msg.inv !== "object") return;
      queue(info, async () => physics.send({ op: "eco_check", player: info.playerId,
                                             inv: msg.inv, almas: msg.almas || null }));
      break;
    }
    case "chest": {
      if (!validCell(msg.cell) || !Array.isArray(msg.entries)) return;
      queue(info, async () => physics.send({ op: "chest", room: room.id, by: info.playerId,
                                             cell: msg.cell.map((v) => v | 0),
                                             entries: msg.entries.slice(0, 64) }));
      break;
    }
    case "bank": {
      if (!validCell(msg.cell)) return;
      const delta = Math.trunc(Number(msg.delta) || 0);
      if (!delta || Math.abs(delta) > 1e9) return;
      queue(info, async () => physics.send({ op: "bank", room: room.id, by: info.playerId,
                                             cell: msg.cell.map((v) => v | 0), delta }));
      break;
    }
    case "icont": {
      if (!economy.CID.test(String(msg.cid || "")) || !Array.isArray(msg.entries)) return;
      queue(info, async () => {
        const r = await economy.itemContainer(info.playerId, String(msg.cid), msg.entries, msg.size);
        if (!r || r.changed) {
          send(peer, { t: "icont_fix", cid: msg.cid, entries: r ? r.entries : null });
        }
      });
      break;
    }
    case "ignite": {
      // isqueiro: o jogador tem de estar perto (o Python confere o isqueiro e o bloco)
      if (!validCell(msg.cell)) return;
      const [ix, iy, iz] = msg.cell.map((v) => v | 0);
      const near = guard.goodPos(info);
      if (near && Math.hypot(near[0] - ix * config.blockSize, near[2] - iz * config.blockSize) > 8 * config.blockSize) return;
      const t = Date.now();
      if (t - (info.lastIgnite || 0) < 250) return;
      info.lastIgnite = t;
      queue(info, async () => physics.send({ op: "ignite", room: room.id, by: info.playerId, cell: [ix, iy, iz] }));
      break;
    }
    case "death": {
      // morreu: o Python passa tudo o que o livro tem para um cadáver ali
      const t = Date.now();
      if (t - (info.lastDeath || 0) < 3000) return;
      info.lastDeath = t;
      const pos = guard.goodPos(info) || (Array.isArray(msg.pos) ? msg.pos.slice(0, 3).map(Number) : null);
      if (!pos || !pos.every(Number.isFinite)) return;
      queue(info, async () => physics.send({ op: "death", room: room.id, by: info.playerId, pos,
                                             yaw: Number(msg.yaw) || 0, nome: info.nome || "" }));
      break;
    }
    case "corpse": {
      // fechou um cadáver: o Python confere (como um baú) e se está perto
      if (typeof msg.id !== "string" || msg.id.length > 40 || !Array.isArray(msg.entries)) return;
      const at = guard.goodPos(info);
      queue(info, async () => physics.send({ op: "corpse", room: room.id, by: info.playerId, id: msg.id,
                                             entries: msg.entries.slice(0, 64), at: at || null }));
      break;
    }
    case "buy": {
      // loja do minoculus: o Python confere o preço (blocks.json, portal.loja) e cobra
      if (typeof msg.item !== "string" || msg.item.length > 64) return;
      queue(info, async () => physics.send({ op: "eco_buy", player: info.playerId, item: msg.item }));
      break;
    }
    case "bucket_fill": {
      // quem simula a água: um balde vazio do mundo encheu (o Python confere o bloco)
      if (!validCell(msg.cell) || String(waterHost(room)) !== String(info.playerId)) return;
      physics.send({ op: "bucket_fill", room: room.id, cell: msg.cell.map((v) => v | 0) });
      break;
    }
    case "transmute": {
      queue(info, async () => physics.send({ op: "eco_transmute", player: info.playerId,
                                             idx: msg.idx | 0, item: String(msg.item || ""),
                                             stone_d: Number(msg.stone_d) }));
      break;
    }
    case "test": {
      if (!economy.testMode()) return;
      const kind = ["almas+", "almas-", "balde_agua"].includes(msg.kind) ? msg.kind : null;
      if (kind) queue(info, async () => physics.send({ op: "eco_test", player: info.playerId, kind }));
      break;
    }
    case "water": {
      if (!Array.isArray(msg.changes) || msg.changes.length > WATER_MAX_CHANGES) return;
      const ok = [];
      for (const ch of msg.changes) {
        if (!Array.isArray(ch) || ch.length !== 2) continue;
        const [key, lv] = ch;
        const n = Number(lv) | 0;
        if (!KEY_RE.test(String(key)) || n < 0 || n > 8) continue;
        if (n === 0) room.water.delete(String(key));
        else if (room.water.size < WATER_MAX_CELLS || room.water.has(String(key))) room.water.set(String(key), n);
        else continue;
        ok.push([String(key), n]);
      }
      if (ok.length) {
        rooms.waterChanged(room);                 // vai para o MongoDB (rooms.js)
        broadcast(room, { t: "water", changes: ok }, peer);
        physics.send({ op: "water", room: room.id, changes: ok });   // o fogo apaga-se com água
      }
      break;
    }
    case "crank": {
      // manivela (torque): tem de haver uma ali e o jogador tem de estar perto
      if (!validCell(msg.cell)) return;
      const [cx, cy, cz] = msg.cell.map((v) => v | 0);
      const def = blockDefs()[room.world.get(cx, cy, cz)] || {};
      if (((def.block_properties || {}).torque || {}).tipo !== "manivela") return;
      const good = guard.goodPos(info);
      if (good && Math.hypot(good[0] - cx * config.blockSize, good[2] - cz * config.blockSize) > 8 * config.blockSize) return;
      const now = Date.now();
      if (now - (info.lastCrank || 0) < 120) return;            // no máximo ~8 por segundo
      info.lastCrank = now;
      broadcast(room, { t: "crank", cell: [cx, cy, cz], dir: msg.dir === -1 ? -1 : 1, by: info.playerId }, peer);
      break;
    }
    case "screw": {
      if (!Array.isArray(msg.s) || msg.s.length > 2000) return;
      const ok = [];
      for (const row of msg.s) {
        if (!Array.isArray(row) || row.length !== 2 || !KEY_RE.test(String(row[0])) || !Array.isArray(row[1])) continue;
        const v = row[1].slice(0, 2).map((n) => Math.max(0, Math.min(8, Number(n) | 0)));
        if (v.some((n) => n > 0)) {
          if (room.screws.size >= 5000 && !room.screws.has(String(row[0]))) continue;
          room.screws.set(String(row[0]), v);
        } else {
          room.screws.delete(String(row[0]));
        }
        ok.push([String(row[0]), v]);
      }
      if (ok.length) {
        rooms.waterChanged(room);
        broadcast(room, { t: "screw", s: ok }, peer);
      }
      break;
    }
    case "attack": {
      const kind = typeof msg.kind === "string" ? msg.kind.slice(0, 40) : "golpe";
      // dano: no máximo o que a arma (que ele tem) e a força dele dão
      const chk = guard.checkHit(info, `m${msg.mob}`, msg.damage, kind);
      if (!chk.damage) {
        send(peer, { t: "attack_result", req: msg.req, mob_id: msg.mob, result: "invalido", motivo: chk.motivo });
        break;
      }
      // a origem do golpe tem de ser perto de onde o servidor sabe que ele está
      let origin = Array.isArray(msg.origin) && msg.origin.length === 3 ? msg.origin.map(Number) : null;
      const good = guard.goodPos(info);
      if (good && (!origin || !origin.every(Number.isFinite) || Math.hypot(origin[0] - good[0], origin[2] - good[2]) > 3 * guard.SIZE)) {
        origin = [good[0], good[1] + 2.4, good[2]];
      }
      const r = room.mobs.attack(info.playerId, msg.mob, chk.damage, origin || [0, 0, 0], kind);
      if (r && r.result === "morreu" && r.mob) {
        // almas do mob morto: o servidor dá (o jogo só mostra)
        economy.credit(info.playerId, [], economy.killSouls(r.mob.tipo));
      }
      send(peer, { t: "attack_result", req: msg.req, mob_id: msg.mob, ...r });
      break;
    }
    case "deflect": {
      // a espada do jogador bateu num projétil de mob: o servidor confere e rebate
      if (!Array.isArray(msg.dir) || msg.dir.length !== 3) return;
      room.mobs.deflect(info.playerId, msg.proj, msg.dir);
      break;
    }
    case "pvp": {
      const now = Date.now();
      if (info.lastPvp && now - info.lastPvp < PVP_MIN_INTERVAL) return;
      let victim = null, vinfo = null;
      for (const p of room.peers) {
        const i = peers.get(p);
        if (i && i.playerId === msg.target && p !== peer) { victim = p; vinfo = i; break; }
      }
      if (!victim || !info.state || !vinfo.state || vinfo.state.dead) return;
      const [ax, , az] = info.state.pos;
      const [vx, , vz] = vinfo.state.pos;
      if (Math.hypot(ax - vx, az - vz) > PVP_REACH) return;
      const chk = guard.checkHit(info, `p${msg.target}`, msg.damage, "golpe");
      let damage = Math.min(PVP_MAX_DAMAGE, chk.damage);
      // "arma": acertou em cheio a arma da vítima (caixa de colisão da arma):
      // a arma dela gasta o dano e o corpo não leva.
      // "escudo": acertou o escudo levantado da vítima: o escudo leva tudo.
      const part = ["cabeca", "arma", "escudo"].includes(msg.part) ? msg.part : "corpo";
      if (part === "cabeca") damage *= HEAD_MULT;
      if (damage <= 0) return;
      info.lastPvp = now;
      send(victim, { t: "pvp_hit", from: info.playerId, nome: info.nome, damage, part,
                     pos: info.state.pos });
      send(peer, { t: "pvp_ok", target: msg.target, damage, part });
      break;
    }
    case "bye":
      leave(peer);
      break;
    default:
      break;
  }
}

// Quem simula a água da sala: o jogador ligado de menor id.
function waterHost(room) {
  let best = null;
  for (const p of room.peers) {
    const i = peers.get(p);
    if (i && (best === null || String(i.playerId) < best)) best = String(i.playerId);
  }
  return best;
}

// Fila por jogador: os pedidos que mexem em itens chegam ao Python na ordem
// em que o jogo os mandou (mesmo os que precisam de ler o banco antes).
function queue(info, fn) {
  info.chain = info.chain.then(fn).catch((e) => console.warn("[economia]", e.message));
}

function peerOfPlayer(pid) {
  for (const [p, i] of peers) if (i.playerId === pid) return p;
  return null;
}

function validCell(c) {
  return Array.isArray(c) && c.length === 3 && c.every((v) => Number.isFinite(v) && Math.abs(v) < 1e6);
}

function peerOf(room, playerId) {
  for (const p of room.peers) {
    const i = peers.get(p);
    if (i && i.playerId === playerId) return p;
  }
  return null;
}

// Para outros módulos (HTTP): mandar algo a todos numa sala.
// Sala onde o jogador está ligado agora (ou null).
function roomOfPlayer(pid) {
  for (const info of peers.values()) if (info.playerId === pid) return info.roomId;
  return null;
}

function broadcastRoom(roomId, obj, exceptPlayer = null) {
  const room = rooms.get(roomId);
  if (!room) return;
  for (const p of room.peers) {
    const i = peers.get(p);
    if (!exceptPlayer || !i || i.playerId !== exceptPlayer) send(p, obj);
  }
}

// O que a física (Python) decidiu: grava (diário) e avisa os jogadores.
function onPhysics(ev) {
  if (ev.ev === "log") return console.log("[física]", ev.msg);
  if (ev.ev === "eco_profile") { guard.setProfile(ev.player, ev.profile); return; }
  if (economy.onPhysicsEvent(ev)) return;
  if (ev.ev === "eco_to") {
    const p = peerOfPlayer(String(ev.player));
    if (p) send(p, ev.msg);
    return;
  }
  const room = rooms.get(ev.room);
  if (ev.ev === "to") {
    if (!room) return;
    const p = peerOf(room, ev.player);
    if (p) send(p, ev.msg);
    return;
  }
  if (ev.ev !== "changes") return;
  for (const [k, v] of ev.blocks || []) {
    rooms.setBlock(ev.room, k, v).catch((e) => console.warn("[salas]", e.message));
  }
  const fields = ev.fields || {};
  if (Object.keys(fields).length) {
    storage.setFields(ev.room, fields).catch((e) => console.warn("[salas]", e.message));
  }
  if (!room) return;
  // Animação antes dos blocos: o jogo faz a cópia que cai enquanto o bloco
  // ainda está no lugar (com o modelo e a orientação de antes).
  if ((ev.anims || []).length) broadcast(room, { t: "anim", items: ev.anims });
  if ((ev.blocks || []).length) broadcast(room, { t: "blocks", changes: ev.blocks });
  if (Object.keys(fields).length) broadcast(room, { t: "fields", fields });
  if (fields.fogo !== undefined && room.mobs) room.mobs.setFire(fields.fogo);   // mobs nas chamas queimam
  if ((ev.fx || []).length) broadcast(room, { t: "fx", items: ev.fx });
}

// Bichos que beberam: cada gole tira 1 nível (1/8) da célula de água.
function drinkWater(room) {
  const cells = room.mobs.drainDrinks ? room.mobs.drainDrinks() : [];
  if (!cells.length) return;
  const ok = [];
  for (const c of cells) {
    const key = c.join(",");
    const lv = room.water.get(key) | 0;
    if (lv <= 0) continue;
    const n = lv - 1;
    if (n <= 0) room.water.delete(key); else room.water.set(key, n);
    ok.push([key, n]);
  }
  if (!ok.length) return;
  rooms.waterChanged(room);
  broadcast(room, { t: "water", changes: ok });
  physics.send({ op: "water", room: room.id, changes: ok });
}

function tick(dt) {
  for (const room of rooms.active.values()) {
    room.acc = (room.acc || 0) + dt;
    while (room.acc >= TICK) {
      room.acc -= TICK;
      room.mobs.step(TICK);
    }
    drinkWater(room);
    if (!room.peers.size) continue;
    // Mobs: os perto do jogador vão todos os ticks (20x/s); a lista inteira
    // só a cada FULL_EVERY ticks (4x/s), com "full" para o cliente apagar os
    // que já não existem. O estado vai sem fiabilidade (perder um não atrasa
    // nada: o próximo substitui-o); os eventos (golpes, mortes, drops) vão à
    // parte, fiáveis. Antes: ~9 KB fiáveis por jogador 20x/s com 40 mobs, e
    // uma perda de pacote parava tudo o que vinha atrás (o mundo travava).
    room.snapN = (room.snapN || 0) + 1;
    const full = room.snapN % FULL_EVERY === 0;
    const packed = room.mobs.packed();
    const projectiles = room.mobs.projectileSnapshot();
    const host = waterHost(room);
    const bs = config.blockSize;
    const near2 = (NEAR_MOBS * bs) ** 2;
    const send2 = (SEND_MOBS * bs) ** 2;
    const sendY = SEND_MOBS_Y * bs;
    for (const peer of room.peers) {
      const info = peers.get(peer);
      if (!info) continue;
      const p = info.state && info.state.pos;
      const mobs = [];
      for (const [m, row] of packed) {
        if (!p) { mobs.push(row); continue; }
        const d2 = (m.x - p[0]) ** 2 + (m.z - p[2]) ** 2;
        if (d2 > send2 || Math.abs(m.y - p[1]) > sendY) continue;      // fora do interesse
        if (full || d2 <= near2 || m.action || m.dead) mobs.push(row);
      }
      send(peer, {
        t: "snap", time: room.mobs.clock, host, players: others(room, peer), mobs, full,
        projectiles,
      }, 0, false);
      const events = room.mobs.drainEvents(info.playerId);
      // golpes de mobs podem empurrar o jogador: folga no movimento
      for (const e of events) if (e.type === "player_hit") guard.grantSlack(info, 3);
      if (events.length) send(peer, { t: "ev", events });
    }
  }
}

async function start(port) {
  const host = await startHost(port, {
    onConnect: () => {},
    onMessage,
    onDisconnect: (peer) => leave(peer),
  });
  let last = Date.now();
  setInterval(() => {
    const now = Date.now();
    tick(Math.min(0.5, (now - last) / 1000));
    last = now;
  }, Math.round(TICK * 1000));
  storage.start((id) => rooms.active.has(id));   // diário a cada 0,25 s + fotos
  physics.start(onPhysics, async () => {
    const out = [];
    for (const id of rooms.active.keys()) out.push({ room: id, data: await storage.roomData(id) });
    // os livros dos jogadores ligados voltam depois das salas abrirem
    require("../admin").pushConfig();
    setTimeout(() => economy.reloadAll([...new Set([...peers.values()].map((i) => i.playerId))]), 0);
    return out;
  });
  return host;
}

// Tira um jogador da sala (moderador fechou/interditou): o jogo vai para a
// praça ou para a sala própria.
function kick(peer, motivo) {
  send(peer, { t: "kicked", motivo: String(motivo || "") });
  leave(peer);
}

function broadcastAll(obj) {
  for (const p of peers.keys()) send(p, obj);
}

// Onde o jogador está agora (sala e posição conferida), ou null.
function livePosition(pid) {
  for (const i of peers.values()) {
    if (i.playerId === String(pid)) return { room: i.roomId, pos: guard.goodPos(i) };
  }
  return null;
}

module.exports = { broadcastRoom, broadcastAll, kick, start, peers, roomOfPlayer, livePosition };
