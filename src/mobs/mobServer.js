// Simulação autoritativa dos mobs de UMA sala.
// Os clientes só contam onde estão e pedem golpes; tudo o resto é decidido aqui.
//
// O comportamento de cada tipo vem do mobs.json (ver o README, "Mobs"):
//   * "ataques": golpes corpo a corpo (caixa de colisão da arma ou das mãos,
//     numa janela da animação), golpes de área, projéteis (um, leque, anel),
//     cura e aura. Cada um com alcance, recarga, peso e condições (partes que
//     o mob tem ou perdeu, vida abaixo de X).
//   * "partes": peças que o jogador pode arrancar (espada e escudo do
//     esqueleto): têm vida própria, somem do modelo e dão o item a quem quebrou.
//   * "voo", "distancia" (mantém-se longe), "defesa", "atordoar", "salto",
//     "animations" (papéis -> clipes, com variações e trocas sem uma parte).
// As caixas de colisão saem do próprio modelo glTF (rig.js), em cada instante
// da animação: o corpo, as armas, as mãos e o ponto de onde saem os projéteis.
const fs = require("fs");
const path = require("path");
const config = require("../config");
const { Rig, MobBoxes, boxesIntersect, aabbBox, segmentBoxT } = require("./rig");

const TICK = 0.05;
const GRAVITY = 30.0;
const SAVE_EVERY = 5.0;
const PLAYER_TIMEOUT = 5.0;
const HIT_REACH = 7.0;
const MAX_PROJECTILES = 200;
// Caixas do jogador: cabeça (quarto de cima) e corpo; na cabeça, HEAD_MULT x dano.
const HEAD_FRACTION = 0.25;
const HEAD_MULT = 1.5;
const DEFLECT_REACH = 4.5;     // blocos entre o jogador e o projétil (folga para o ping)
const DEFLECT_SPEED = 1.3;     // rebatido volta mais rápido...
const DEFLECT_DAMAGE = 1.5;    // ...e mais forte

let TYPES = null;
function mobTypes() {
  if (!TYPES) {
    try {
      TYPES = JSON.parse(fs.readFileSync(path.join(config.dataDir, "mobs.json"), "utf8"));
    } catch (e) {
      console.warn("[mobs] mobs.json ilegível:", e.message);
      TYPES = {};
    }
  }
  return TYPES;
}

function modelPath(tipo) {
  const cfg = mobTypes()[tipo];
  if (!cfg || !cfg.model) return null;
  for (const p of [path.join(config.dataDir, "assets", "models", cfg.model),
                   path.join(config.dataDir, cfg.model)]) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

const rand = (a, b) => a + Math.random() * (b - a);
const pick = (v) => (Array.isArray(v) ? v[Math.floor(Math.random() * v.length)] : v);
const angDiff = (a, b) => ((((a - b + 180) % 360) + 360) % 360) - 180;
const norm3 = (v) => { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; };

// Fome e sede (bichos pacíficos, os que têm "passivo" no mobs.json, em
// qualquer sala): dois cronómetros. A cada HUNGER_TIME s o bicho tem de
// comer — vai ao capim mais perto, come-o ("comendo") e o capim some do mundo
// (worker Python, op "graze"). A cada THIRST_TIME s tem de beber — vai à água
// mais perto, bebe ("bebendo") e essa célula de água desce 1 nível (1/8).
// Se não acha comida/água, o cronómetro fica "vencido" e ele volta a procurar.
const HUNGER_TIME = 10;         // s entre refeições
const THIRST_TIME = 180;        // s entre goles (3 min)
const NEED_AT = 1.0;            // cronómetro vencido
const NEED_RADIUS = 14;         // blocos
const NEED_SEARCH_EVERY = 2.5;  // s entre procuras
const EAT_TIME = 2.0;           // s a comer / beber (dura a animação)
const NEED_GIVE_UP = 15;        // s a tentar chegar antes de desistir
const GRASS_TUFT = "capim";

const FIRE_DAMAGE = 2.5;        // dano do fogo nos mobs, por toque
const FIRE_TICK = 0.5;          // s entre toques

class Mob {
  constructor(id, tipo, pos, yaw = 0, health = null, parts = null) {
    const cfg = mobTypes()[tipo] || {};
    this.id = id;
    this.tipo = tipo;
    [this.x, this.y, this.z] = pos.map(Number);
    this.yaw = Number(yaw) || 0;
    this.health = health === null || health === undefined ? Number(cfg.health || 20) : Number(health);
    this.vy = 0;
    this.state = "idle";
    this.stateT = 0;
    this.anim = null;
    this.animStart = 0;
    this.animRate = 1;          // velocidade do clipe (ataques mais rápidos: "velocidade")
    this.target = null;
    this.home = [this.x, this.y, this.z];
    this.goal = null;
    this.cds = {};                 // recargas por ataque
    this.blockCd = 0;
    this.action = null;            // ataque/ação em curso: { name, spec, fired:Set, hit:Set }
    this.think = rand(0.5, 2.0);
    this.dead = false;
    this.dyingT = 0;
    this.buffT = 0;                // aura (reduz o dano)
    this.flying = !!(cfg.voo && cfg.voo.sempre);
    this.fallTop = this.y;
    this.strafe = Math.random() < 0.5 ? 1 : -1;
    this.strafeT = 0;
    this.hunger = Math.random() * 0.8;   // cronómetros 0-1 (1 = tem de comer/beber)
    this.thirst = Math.random() * 0.8;
    this.need = null;                    // { kind: "comer"|"beber", cell, t, eat }
    this.needSearchT = rand(0, NEED_SEARCH_EVERY);
    // partes (espada, escudo...): vida de cada uma; 0 = perdida
    this.parts = {};
    for (const [name, p] of Object.entries(cfg.partes || {})) {
      const v = parts && parts[name] !== undefined ? Number(parts[name]) : Number(p.vida || 20);
      this.parts[name] = Math.max(0, v);
    }
  }
}

class MobServer {
  constructor(roomId, world, store, stored = []) {
    this.roomId = roomId;
    this.world = world;
    this.size = config.blockSize;
    this.store = store;                 // async (roomId, fields)
    this.mobs = new Map();
    this.players = new Map();           // id -> estado
    this.events = new Map();            // id -> [eventos]
    this.drinks = [];                   // células de água bebidas (rooms/server tiram 1 nível)
    this.projectiles = new Map();
    this.nextProj = 1;
    this.clock = 0;
    this.sinceSave = 0;
    this.dirty = false;
    this.nextId = 1;
    this.spawnT = {};
    this.boxCache = {};
    this.extentCache = {};
    this.publica = false;
    // "publica" (praça, nível 1), "dungeon" (nível 2+) ou "privada": escolhe
    // a tabela "spawn" de cada mob em mobs.json (ver spawnSpec).
    this.zona = "privada";
    this.andares = null;               // dungeon: y (células) de cada piso
    this.room = null;                  // dados da sala (tamanho) p/ espalhar mobs
    this.filled = {};                  // tipo -> já encheu a população inicial
    this.onBlockHit = null;            // (célula, dano, direção): projétil acertou um bloco
    // Lê já os modelos (a sombra tem 6 MB): sem isto o primeiro mob de cada
    // tipo a aparecer parava a sala ~0,1 s.
    for (const tipo of Object.keys(mobTypes())) {
      try { this.boxesFor(tipo); this.extent(tipo); } catch (e) { console.warn(`[mobs] ${tipo}: ${e.message}`); }
    }
    for (const e of stored || []) {
      if (!mobTypes()[e.tipo]) continue;
      if (mobTypes()[e.tipo].ninho && !e.nest) continue;     // fada sem berço: nasce outra
      const m = new Mob(String(e.id), e.tipo, e.pos, e.yaw, e.health, e.parts);
      if (e.nest) {
        m.nest = String(e.nest);
        const [hx, hy, hz] = m.nest.split(",").map(Number);
        m.home = [hx * this.size, (hy + 0.5) * this.size, hz * this.size];
      }
      this.mobs.set(m.id, m);
      this.setState(m, "idle");
      const n = parseInt(String(m.id).split("-").pop(), 10);
      if (!Number.isNaN(n)) this.nextId = Math.max(this.nextId, n + 1);
    }
  }

  cfg(m) { return mobTypes()[m.tipo] || {}; }

  boxesFor(tipo) {
    if (!(tipo in this.boxCache)) {
      const p = modelPath(tipo);
      const cfg = mobTypes()[tipo] || {};
      this.boxCache[tipo] = p ? new MobBoxes(Rig.load(p), cfg.hitboxes || {}, (cfg.scale || 1) * this.size) : null;
    }
    return this.boxCache[tipo];
  }

  // Tamanho do corpo em repouso (no mundo): raio, base e topo (relativos aos pés).
  extent(tipo) {
    if (!(tipo in this.extentCache)) {
      const mb = this.boxesFor(tipo);
      let ext = { r: this.size * 0.35, lo: 0, hi: this.size * 2 };
      if (mb && mb.defs.corpo) {
        const b = mb.boxes([0, 0, 0], 0, null, 0, ["corpo"]).corpo;
        const span = (k) => b.a.reduce((s, ax, i) => s + Math.abs(ax[k]) * b.h[i], 0);
        ext = { r: Math.max(span(0), span(2)) * 0.8, lo: b.c[1] - span(1), hi: b.c[1] + span(1) };
      }
      this.extentCache[tipo] = ext;
    }
    return this.extentCache[tipo];
  }

  // -- partes e animações -------------------------------------------------
  hasPart(m, name) { return (m.parts[name] || 0) > 0; }

  missingParts(m) { return Object.keys(m.parts).filter((p) => !this.hasPart(m, p)); }

  // Clipe de um papel (idle, walk, attack...): sem uma parte pode trocar
  // ("sem_partes": {"espada": {"walk": "walk_2"}}); listas = variações.
  clipFor(m, role) {
    const cfg = this.cfg(m);
    for (const p of this.missingParts(m)) {
      const alt = ((cfg.sem_partes || {})[p] || {})[role];
      if (alt) return pick(alt);
    }
    const v = (cfg.animations || {})[role];
    return v ? pick(v) : role;
  }

  clipLength(m, clip) {
    const mb = this.boxesFor(m.tipo);
    if (!mb || !mb.rig.clips[clip]) return 1.0;
    return mb.rig.clips[clip].duration;
  }

  // Estado da IA + papel da animação. O clipe só muda quando o papel muda
  // (a variação escolhida numa lista fica até lá).
  setState(m, state, role = null) {
    role = role || state;
    if (m.state !== state) { m.state = state; m.stateT = 0; }
    if (m.role !== role || !m.anim) {
      m.role = role;
      m.anim = this.clipFor(m, role);
      m.animStart = this.clock;
      m.animRate = 1;
    }
  }

  playClip(m, state, clip, offset = 0, rate = 1) {
    m.state = state; m.stateT = 0; m.role = state;
    m.anim = clip; m.animStart = this.clock - offset; m.animRate = rate;
  }

  // Tempo dentro do clipe atual (com a velocidade dele).
  animTime(m) { return (this.clock - m.animStart) * (m.animRate || 1); }

  mobBoxes(m, only = null) {
    const mb = this.boxesFor(m.tipo);
    if (!mb) return {};
    // "yaw_offset": modelos feitos de frente para +Z (a sombra) giram 180°
    const yaw = m.yaw + Number(this.cfg(m).yaw_offset || 0);
    return mb.boxes([m.x, m.y, m.z], yaw, m.anim, this.animTime(m), only);
  }

  event(pid, ev) {
    if (!this.events.has(pid)) this.events.set(pid, []);
    this.events.get(pid).push(ev);
  }

  broadcast(ev) { for (const pid of this.players.keys()) this.event(pid, ev); }

  drainDrinks() {
    const d = this.drinks;
    this.drinks = [];
    return d;
  }

  drainEvents(pid) {
    const ev = this.events.get(pid) || [];
    this.events.delete(pid);
    return ev;
  }

  // ------------------------------------------------------------ jogadores
  updatePlayer(pid, state) {
    const prev = this.players.get(pid);
    const p = { ...state, seen: Date.now() / 1000 };
    p.swingNew = !!(prev && (state.swing || 0) !== (prev.swing || 0)) || !!(prev && prev.swingNew);
    // velocidade: medida a cada tick em step() (os mobs antecipam quem foge)
    p.vel = prev && prev.vel ? prev.vel : [0, 0];
    p.tickPos = prev && prev.tickPos ? prev.tickPos : null;
    this.players.set(pid, p);
  }

  removePlayer(pid) {
    this.players.delete(pid);
    this.events.delete(pid);
  }

  activePlayers() {
    const now = Date.now() / 1000;
    const out = new Map();
    for (const [pid, p] of this.players) {
      if (now - p.seen <= PLAYER_TIMEOUT && !p.dead && Array.isArray(p.pos)) out.set(pid, p);
    }
    return out;
  }

  // Dano dos mobs no jogador: físico (a armadura corta) ou mágico ("magico":
  // true no ataque/projétil/explosão em mobs.json: a resistência mágica corta).
  // ------------------------------------------------------------ pedidos
  // kind: "golpe" (arma/soco), "impacto" (encontrão), "parte:<nome>" (a arma
  // do jogador acertou em cheio uma parte do mob, p.ex. a espada do esqueleto),
  // "empurrao" (empurrão com escudo do jogador: não gasta o escudo do mob, abre-
  // lhe a defesa — block_break, o golpe seguinte leva broken_damage_mult — e,
  // sem escudo levantado, atordoa e empurra mais longe).
  attack(pid, mobId, damage, origin, kind = "golpe") {
    const m = this.mobs.get(String(mobId));
    if (!m || m.dead) return { result: "invalido", motivo: "mob não existe" };
    let ox, oy, oz;
    try {
      [ox, oy, oz] = origin.map(Number);
      damage = Math.max(0, Number(damage));
      if (![ox, oy, oz, damage].every(Number.isFinite)) throw new Error();
    } catch (e) {
      return { result: "invalido", motivo: "pedido malformado" };
    }
    const dist = Math.hypot(ox - m.x, oz - m.z) / this.size;
    if (kind !== "impacto" && kind !== "projetil" && dist > HIT_REACH) return { result: "invalido", motivo: "longe demais" };
    damage = Math.min(damage, 200);       // um cliente não pode mandar 9999
    const cfg = this.cfg(m);
    const block = cfg.block || {};
    m.target = pid;
    this.anger(m, pid);
    this.dirty = true;

    if (String(kind).startsWith("parte:")) {
      const part = String(kind).slice(6);
      if (!this.hasPart(m, part)) return { result: "invalido", motivo: "parte já não existe" };
      return this.damagePart(m, part, damage, pid);
    }
    const bash = kind === "empurrao";
    if (bash && this.shieldUp(m)) {
      m.action = null;
      this.playClip(m, "block_break", this.clipFor(m, "block_break"));
      return { result: "defesa_aberta", damage: 0, mob: this.pub(m) };
    }
    // escudo levantado: o golpe que atravessa o escudo vai para ele
    if (kind === "golpe" && this.shieldUp(m)) {
      const hb = block.hitbox || "escudo";
      const boxes = this.mobBoxes(m, [hb, "corpo"]);
      if (boxes[hb] && boxes.corpo && segmentBoxT([ox, oy, oz], boxes.corpo.c, boxes[hb]) !== null) {
        const part = block.parte || "escudo";
        const r = this.damagePart(m, part, damage, pid);
        if (r.result === "parte_quebrou") {
          this.playClip(m, "block_break", this.clipFor(m, "block_break"));
          return { ...r, result: "escudo_quebrou" };
        }
        return { ...r, result: "bloqueou" };
      }
    }
    if (m.state === "block_break") damage *= Number(block.broken_damage_mult || 1);
    const def = cfg.defesa || {};
    if (!bash && (m.state === "defend" || (m.state === "defend_start" && m.stateT > 0.12)) && def.reducao) {
      damage *= 1 - Number(def.reducao);
    }
    const aura = (cfg.ataques || {}).aura;
    if (m.buffT > 0 && aura) damage *= 1 - Number(aura.reducao || 0.5);
    m.health -= damage;
    if (m.health <= 0) {
      this.kill(m);
      return { result: "morreu", damage, mob: this.pub(m) };
    }
    // atordoar: golpe forte interrompe (animação "hurt")
    const st = cfg.atordoar;
    if (((st && damage >= Number(st.limite || 0.1) * Number(cfg.health || 20)
        && Math.random() < Number(st.chance || 1)) || bash) && !["morrendo", "special"].includes(m.state)) {
      m.action = null;
      this.playClip(m, "hurt", this.clipFor(m, "hurt"));
    }
    if (!m.flying) {
      const dx = m.x - ox, dz = m.z - oz;
      const n = Math.hypot(dx, dz) || 1;
      const push = bash ? 1.5 : 0.5;          // o empurrão com escudo afasta mais
      this.move(m, (dx / n) * this.size * push, (dz / n) * this.size * push);
    }
    return { result: "acertou", damage, mob: this.pub(m) };
  }

  damagePart(m, part, damage, pid) {
    m.parts[part] = Math.max(0, (m.parts[part] || 0) - damage);
    this.dirty = true;
    if (m.parts[part] > 0) return { result: "parte_acertou", parte: part, damage, mob: this.pub(m) };
    const spec = (this.cfg(m).partes || {})[part] || {};
    if (spec.item) {
      this.event(pid, { type: "mob_drop", item: spec.item, count: 1, mob: m.id, tipo: m.tipo, parte: part });
      require("../economy").credit(pid, [[spec.item, 1]], 0);     // o servidor dá o item
    }
    this.broadcast({ type: "mob_part_broken", mob: m.id, tipo: m.tipo, parte: part,
      pos: [m.x, m.y + this.extent(m.tipo).hi * 0.6, m.z] });
    if (m.action && (m.action.spec.requer || []).includes(part)) m.action = null;
    m.role = null;                  // sem a parte, os clipes podem mudar (walk -> walk_2)
    return { result: "parte_quebrou", parte: part, item: spec.item || null, damage, mob: this.pub(m) };
  }

  kill(m) {
    m.dead = true;
    m.health = 0;
    m.action = null;
    this.broadcast({ type: "mob_died", mob: m.id, tipo: m.tipo, pos: [m.x, m.y, m.z] });
    const boom = (this.cfg(m).morte || {}).explosao;
    const death = (this.cfg(m).animations || {}).death;
    if (boom) {
      // Explode: incha durante "inchar" s (o cliente mostra) e depois rebenta.
      m.explode = boom;
      m.dyingT = Math.max(0.05, Number(boom.inchar ?? 0.45));
    } else if (death) {
      this.playClip(m, "morrendo", pick(death));
      m.dyingT = Math.min(4.0, this.clipLength(m, m.anim));
    } else {
      this.mobs.delete(m.id);
    }
  }

  // Explosão de morte ("morte": {"explosao": {raio, dano, dano_min}} no
  // mobs.json): dano que cai com a distância a quem estiver no raio (blocos).
  // O dano vem "de" o centro da explosão, por isso o escudo pode bloquear.
  explode(m, players) {
    const spec = m.explode || {};
    const bs = this.size;
    const R = Number(spec.raio || 3) * bs;
    const ext = this.extent(m.tipo);
    const c = [m.x, m.y + (ext.lo + ext.hi) * 0.5, m.z];
    const top = Number(spec.dano || 3), low = Number(spec.dano_min ?? 1);
    for (const [pid, pl] of players) {
      if (!Array.isArray(pl.pos)) continue;
      const h = Number(pl.height || 3.0);
      const py = Math.max(pl.pos[1], Math.min(c[1], pl.pos[1] + h));   // ponto do corpo mais perto
      const d = Math.hypot(pl.pos[0] - c[0], py - c[1], pl.pos[2] - c[2]);
      const hw = Number(pl.half_width || 0.45);
      if (d > R + hw) continue;
      const k = Math.max(0, Math.min(1, d / R));
      const damage = Math.round((top + (low - top) * k) * 10) / 10;
      if (damage <= 0) continue;
      this.event(pid, { type: "player_hit", mob: m.id, tipo: m.tipo, damage,
        part: this.shieldCovers(pl, c) ? "escudo" : "corpo",
        kind: "explosao", from: c, empurrao: Number(spec.empurrao || 0), magico: !!spec.magico });
    }
    this.broadcast({ type: "mob_fx", fx: "explosao", pos: c, raio: R, tipo: m.tipo,
      cor: spec.cor || null, chao: m.y });
  }

  spawn(tipo, pos = null) {
    if (!mobTypes()[tipo]) return { result: "invalido", motivo: `tipo ${tipo} não existe` };
    if (!pos) pos = this.spawnPoint(tipo);
    if (!pos) return { result: "invalido", motivo: "sem lugar livre" };
    const m = new Mob(`mob-${this.nextId++}`, tipo, pos, rand(0, 360));
    this.setState(m, "idle");
    this.mobs.set(m.id, m);
    this.dirty = true;
    return { result: "ok", mob: this.pub(m) };
  }

  // Painel: mata os mobs (de um tipo, ou todos). Sem drops nem almas.
  adminKill(tipo = null) {
    let n = 0;
    for (const m of [...this.mobs.values()]) {
      if (m.dead || (tipo && m.tipo !== tipo)) continue;
      m.dead = true;
      m.health = 0;
      this.broadcast({ type: "mob_died", mob: m.id, tipo: m.tipo, pos: [m.x, m.y, m.z] });
      this.mobs.delete(m.id);
      n += 1;
    }
    this.dirty = true;
    return n;
  }

  snapshot() { return [...this.mobs.values()].map((m) => this.pub(m)); }

  // Snapshot compacto (o que vai 20x/s pela rede): uma lista curta por mob,
  // [id, tipo, x, y, z, yaw, vida, clipe, tempo do clipe, extra?]; "extra"
  // só leva o que não é o normal (partes, escudo, a morrer, aura).
  packed() {
    const r2 = (v) => Math.round(v * 100) / 100;
    const out = [];
    for (const m of this.mobs.values()) {
      const cfg = this.cfg(m);
      const row = [m.id, m.tipo, r2(m.x), r2(m.y), r2(m.z), Math.round(m.yaw),
        Math.round(Math.max(0, m.health) * 10) / 10, m.anim, r2(this.animTime(m))];
      const extra = {};
      const hidden = [];
      for (const [name, p] of Object.entries(cfg.partes || {})) {
        if (!this.hasPart(m, name) && p.bone) hidden.push(p.bone);
      }
      if (hidden.length) extra.hidden = hidden;
      if (Object.keys(m.parts).length) extra.parts = m.parts;
      const shieldPart = (cfg.block || {}).parte || "escudo";
      const shieldSpec = (cfg.partes || {})[shieldPart];
      if (shieldSpec) { extra.shield = m.parts[shieldPart] || 0; extra.shield_max = Number(shieldSpec.vida || 0); }
      if (m.dead) extra.dying = true;
      if (m.buffT > 0) extra.aura = true;
      if ((m.animRate || 1) !== 1) extra.rate = m.animRate;
      if (Object.keys(extra).length) row.push(extra);
      out.push([m, row]);
    }
    return out;
  }

  projectileSnapshot() {
    const r = (v) => Math.round(v * 1000) / 1000;
    return [...this.projectiles.values()].map((p) => ({
      id: p.id, tipo: p.tipo, kind: p.kind, pos: p.pos.map(r), vel: p.vel.map(r),
      ...(p.deflected ? { rebatido: true } : {}),
    }));
  }

  pub(m) {
    const cfg = this.cfg(m);
    const r = (v) => Math.round(v * 1000) / 1000;
    const hidden = [];
    for (const [name, p] of Object.entries(cfg.partes || {})) {
      if (!this.hasPart(m, name) && p.bone) hidden.push(p.bone);
    }
    const shieldPart = (cfg.block || {}).parte || "escudo";
    const shieldSpec = (cfg.partes || {})[shieldPart];
    return {
      id: m.id, tipo: m.tipo, pos: [r(m.x), r(m.y), r(m.z)], yaw: r(m.yaw),
      health: Math.max(0, m.health), max_health: Number(cfg.health || 20),
      state: m.state, anim: m.anim, anim_time: r(this.animTime(m)), rate: m.animRate || 1,
      shield: shieldSpec ? (m.parts[shieldPart] || 0) : 0,
      shield_max: shieldSpec ? Number(shieldSpec.vida || 0) : 0,
      hidden, parts: { ...m.parts }, dying: !!m.dead, aura: m.buffT > 0,
    };
  }

  async save() {
    const data = [...this.mobs.values()].filter((m) => !m.dead).map((m) => ({
      id: m.id, tipo: m.tipo,
      pos: [m.x, m.y, m.z].map((v) => Math.round(v * 1000) / 1000),
      yaw: Math.round(m.yaw * 10) / 10, health: Math.round(m.health * 100) / 100,
      parts: m.parts, nest: m.nest || undefined,
    }));
    this.dirty = false;
    try {
      await this.store(this.roomId, { mobs: data });
    } catch (e) {
      console.warn("[mobs] não foi possível gravar:", e.message);
    }
  }

  // ------------------------------------------------------------ fogo
  // O estado do fogo (campo "fogo" da sala, do worker Python): mobs nas
  // chamas queimam (FIRE_DAMAGE a cada FIRE_TICK s, sem defesa). Morrer
  // queimado não dá drops nem almas a ninguém.
  setFire(state) {
    const cells = new Set();
    for (const [k, v] of Object.entries(state || {})) {
      const c = String(k).split(",").map(Number);
      if (c.length !== 3 || !c.every(Number.isFinite)) continue;
      cells.add(`${c[0]},${c[1]},${c[2]}`);
      if (!(Array.isArray(v) && v[2])) {          // fogo num bloco: chamas por cima
        cells.add(`${c[0]},${c[1] + 1},${c[2]}`);
        cells.add(`${c[0]},${c[1] + 2},${c[2]}`);
      }
    }
    this.fireCells = cells;
  }

  burnMobs(dt) {
    if (!this.fireCells || !this.fireCells.size) return;
    this.fireT = (this.fireT || 0) + dt;
    if (this.fireT < FIRE_TICK) return;
    this.fireT = 0;
    const s = this.size;
    for (const m of [...this.mobs.values()]) {
      if (m.dead) continue;
      const cx = Math.floor(m.x / s + 0.5), cy = Math.floor(m.y / s + 0.5), cz = Math.floor(m.z / s + 0.5);
      if (!this.fireCells.has(`${cx},${cy},${cz}`) && !this.fireCells.has(`${cx},${cy + 1},${cz}`)) continue;
      m.health -= FIRE_DAMAGE;
      this.dirty = true;
      this.broadcast({ type: "mob_burn", mob: m.id, damage: FIRE_DAMAGE, pos: [m.x, m.y, m.z] });
      if (m.health <= 0) this.kill(m);
    }
  }

  // ------------------------------------------------------------ simulação
  step(dt = TICK) {
    const players = this.activePlayers();
    if (!players.size) return;
    this.clock += dt;
    this.burnMobs(dt);
    // velocidade de cada jogador no chão (blocos de mundo/s), suavizada
    for (const p of players.values()) {
      if (!Array.isArray(p.pos)) continue;
      if (p.tickPos) {
        const vx = (p.pos[0] - p.tickPos[0]) / dt, vz = (p.pos[2] - p.tickPos[2]) / dt;
        const pv = p.vel || [0, 0];
        p.vel = [pv[0] * 0.6 + vx * 0.4, pv[1] * 0.6 + vz * 0.4];
      }
      p.tickPos = [p.pos[0], p.pos[1], p.pos[2]];
    }
    for (const m of [...this.mobs.values()]) {
      if (m.dead) {
        m.dyingT -= dt;
        if (m.dyingT <= 0) {
          if (m.explode) this.explode(m, players);
          this.mobs.delete(m.id);
        }
        continue;
      }
      this.thinkMob(m, players, dt);
      this.physics(m, players, dt);
    }
    this.separate();
    this.stepProjectiles(players, dt);
    this.spawner(dt);
    this.nests(dt);
    for (const p of players.values()) p.swingNew = false;
    this.sinceSave += dt;
    if (this.sinceSave >= SAVE_EVERY) {
      this.sinceSave = 0;
      if (this.dirty) this.save();
    }
  }

  // Passivos ("passivo" em mobs.json): não procuram ninguém; quem os bate
  // fica com a raiva deles (e dos do mesmo tipo à volta) durante "raiva" s.
  anger(m, pid) {
    const pas = this.cfg(m).passivo || (this.cfg(m).ninho ? { raiva: 20, bando: 8 } : null);
    if (!pas) return;
    const t = Number(pas.raiva || 15);
    const R = Number(pas.bando || 0) * this.size;
    for (const o of this.mobs.values()) {
      if (o.dead || o.tipo !== m.tipo) continue;
      if (o !== m && (R <= 0 || Math.hypot(o.x - m.x, o.z - m.z) > R)) continue;
      o.angryPid = pid;
      o.angryT = t;
    }
  }

  pickTarget(m, players) {
    const cfg = this.cfg(m);
    const bs = this.size;
    // guardas do berço (fadas): atacam quem chega perto do berço; largam
    // quando o jogador se afasta dele (ou quando são atacadas: raiva normal)
    if (cfg.ninho && m.nest) {
      const g = cfg.ninho;
      const [hx, hy, hz] = m.home;
      let best = null, bd = 1e9;
      for (const [pid, p] of players) {
        const d = Math.hypot(p.pos[0] - hx, p.pos[2] - hz) / bs;
        const dy = Math.abs(p.pos[1] - hy) / bs;
        const lim = Number(pid === m.target ? (g.largar || 14) : (g.guarda || 6));
        if ((d <= lim && dy < 8) || (m.angryT > 0 && pid === m.angryPid)) {
          const dm = Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / bs;
          if (dm < bd) { best = pid; bd = dm; }
        }
      }
      m.target = best;
      return best ? [players.get(best), bd] : [null, 1e9];
    }
    if (cfg.passivo) {
      const p = m.angryT > 0 ? players.get(m.angryPid) : null;
      const d = p ? Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / bs : 1e9;
      if (!p || d > Number(cfg.lose_range || 16)) { m.target = null; return [null, 1e9]; }
      m.target = m.angryPid;
      return [p, d];
    }
    let target = null, tdist = 1e9;
    for (const [pid, p] of players) {
      const d = Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / bs;
      const limit = Number(cfg[pid === m.target ? "lose_range" : "detect_range"] || 12);
      const dy = Math.abs(p.pos[1] - m.y) / bs;
      if (d <= limit && dy < (m.flying || cfg.voo ? 14 : 6) && d < tdist) { target = pid; tdist = d; }
    }
    m.target = target;
    return [target ? players.get(target) : null, tdist];
  }

  thinkMob(m, players, dt) {
    const cfg = this.cfg(m);
    const block = cfg.block || {};
    m.stateT += dt;
    for (const k of Object.keys(m.cds)) m.cds[k] = Math.max(0, m.cds[k] - dt);
    m.blockCd = Math.max(0, m.blockCd - dt);
    m.buffT = Math.max(0, m.buffT - dt);
    if (m.angryT > 0) m.angryT -= dt;
    const [p, tdist] = this.pickTarget(m, players);

    // ações que não se interrompem por decisão própria
    if (m.action) return this.actionTick(m, p, players, dt);
    if (m.state === "hurt" || m.state === "pousar" || m.state === "virar") {
      if (m.state === "virar" && p) this.face(m, p.pos, cfg, dt, 0.6);
      if (m.stateT >= this.clipLength(m, m.anim) * (m.state === "virar" ? 0.8 : 1)) this.setState(m, "idle");
      return;
    }
    if (m.state === "salto") {
      if (p) { this.face(m, p.pos, cfg, dt); this.walkTowards(m, p.pos, Number(cfg.walk_speed || 2) * this.size, dt); }
      if (m.vy <= 0 && this.onGround(m)) this.setState(m, "idle");
      return;
    }
    if (m.state === "block_start") {
      if (p) this.face(m, p.pos, cfg, dt);
      if (m.stateT >= this.clipLength(m, m.anim)) this.setState(m, "block");
      return;
    }
    if (m.state === "block") {
      if (p) this.face(m, p.pos, cfg, dt);
      if (m.stateT >= Number(block.hold || 2) || !this.hasPart(m, block.parte || "escudo")) this.setState(m, "block_end");
      return;
    }
    if (m.state === "block_end") {
      if (m.stateT >= this.clipLength(m, m.anim) * 0.6) { m.blockCd = Number(block.cooldown || 3); this.setState(m, "idle"); }
      return;
    }
    if (m.state === "block_break") {
      if (m.stateT >= this.clipLength(m, m.anim)) { m.blockCd = Number(block.cooldown || 3); this.setState(m, "idle"); }
      return;
    }
    const def = cfg.defesa;
    if (m.state === "defend_start") {
      if (p) this.face(m, p.pos, cfg, dt);
      if (m.stateT >= this.clipLength(m, m.anim)) this.setState(m, "defend");
      return;
    }
    if (m.state === "defend") {
      if (p) this.face(m, p.pos, cfg, dt);
      if (m.stateT >= Number((def || {}).hold || 1.5)) { m.blockCd = Number((def || {}).cooldown || 3); this.setState(m, "idle"); }
      return;
    }
    if (!p) return this.wander(m, cfg, dt);

    // reagir ao golpe do jogador: escudo (se ainda o tem) ou defesa com os braços
    if (p.swingNew && m.blockCd <= 0 && tdist <= Number(block.range || 3)) {
      if (cfg.block && this.hasPart(m, block.parte || "escudo") && Math.random() < Number(block.chance || 0.4)) {
        this.setState(m, "block_start");
        return;
      }
      const missing = this.missingParts(m);
      if (def && (def.sem || []).every((x) => missing.includes(x)) && Math.random() < Number(def.chance || 0.35)) {
        this.setState(m, "defend_start");
        return;
      }
    }
    // virar no lugar (sombra): alvo atrás e parado
    const turnDiff = angDiff((Math.atan2(p.pos[0] - m.x, p.pos[2] - m.z) * 180) / Math.PI, m.yaw);
    const anims = cfg.animations || {};
    if (anims.turn_left && Math.abs(turnDiff) > 110 && !m.flying && m.state === "idle" && m.stateT > 0.3) {
      this.setState(m, "virar", turnDiff > 0 ? "turn_left" : "turn_right");
      return;
    }
    // escolher um ataque
    const choice = this.chooseAttack(m, p, tdist);
    if (choice) {
      this.startAction(m, choice[0], choice[1]);
      return;
    }
    // saltar para um alvo em cima (sombra)
    const jump = cfg.salto;
    const dyBlocks = (p.pos[1] - m.y) / this.size;
    if (jump && !m.flying && this.onGround(m) && dyBlocks >= 1.2 && dyBlocks <= Number(jump.altura || 3)
        && tdist <= Number(jump.distancia || 5) && (m.cds.salto || 0) <= 0) {
      m.vy = Math.sqrt(2 * GRAVITY * (dyBlocks + 0.6) * this.size);
      m.cds.salto = Number(jump.cooldown || 3);
      this.setState(m, "salto", "jump");
      return;
    }
    this.positioning(m, p, tdist, dt);
  }

  // -- escolher e executar ataques -----------------------------------------
  chooseAttack(m, p, tdist) {
    const cfg = this.cfg(m);
    const missing = this.missingParts(m);
    const facing = this.facing(m, p.pos);
    const hpFrac = m.health / Number(cfg.health || 20);
    const options = [];
    for (const [name, a] of Object.entries(cfg.ataques || {})) {
      if ((m.cds[name] || 0) > 0) continue;
      if ((a.requer || []).some((x) => missing.includes(x))) continue;
      if ((a.sem || []).some((x) => !missing.includes(x))) continue;
      if (a.vida_abaixo !== undefined && hpFrac > Number(a.vida_abaixo)) continue;
      if (a.tipo === "aura" && m.buffT > 0) continue;
      const [rmin, rmax] = a.alcance || [0, Number(cfg.attack_range || 1.6)];
      if (tdist < rmin || tdist > rmax) continue;
      // golpe corpo a corpo: onde o alvo vai estar quando o golpe chegar? (quem
      // foge sai do alcance durante a preparação: não vale começar)
      if (a.tipo === "corpo" && p.vel) {
        const bs = this.size;
        const dx = p.pos[0] - m.x, dz = p.pos[2] - m.z, n = Math.hypot(dx, dz) || 1;
        const away = (p.vel[0] * dx + p.vel[1] * dz) / n / bs;           // blocos/s a afastar-se
        if (away > 0.3) {
          const rate = Number(a.velocidade || cfg.velocidade_ataque || 1);
          const tHit = Number((a.janela || [0.3])[0]) / rate;
          const chase = a.perseguir === false ? 0 : Number(cfg.run_speed || 3) * Number(a.perseguir_vel || 1);
          if (tdist + (away - chase) * tHit > rmax * 0.95) continue;
        }
      }
      if (facing > Number(a.mirar || (a.tipo === "corpo" ? 35 : 60))) continue;
      if (a.voando === false && m.flying) continue;
      // "alcance_ideal": [min, max] em blocos: nessa distância o golpe é
      // escolhido 4x mais (corte perto, estocada mais longe)
      const ideal = a.alcance_ideal;
      const w = Number(a.peso || 1) * (ideal && tdist >= ideal[0] && tdist <= ideal[1] ? 4 : 1);
      options.push([name, a, w]);
    }
    if (!options.length) return null;
    let r = Math.random() * options.reduce((s, o) => s + o[2], 0);
    for (const o of options) { r -= o[2]; if (r <= 0) return o; }
    return options[options.length - 1];
  }

  startAction(m, name, spec) {
    m.action = { name, spec, fired: new Set(), hit: new Set() };
    m.cds[name] = Number(spec.cooldown || 2);
    // "velocidade": o clipe toca mais depressa (1,3 = 30% mais rápido); as
    // janelas e tempos do ataque continuam em tempo do clipe.
    const rate = Math.max(0.2, Number(spec.velocidade || this.cfg(m).velocidade_ataque || 1));
    this.playClip(m, spec.tipo === "aura" || spec.tipo === "cura" ? "special" : "attack",
      spec.clip || this.clipFor(m, "attack"), 0, rate);
    m.action.length = Math.min(Number(spec.duracao || 1e9), this.clipLength(m, m.anim));   // tempo do clipe
  }

  actionTick(m, p, players, dt) {
    const cfg = this.cfg(m);
    const a = m.action;
    const s = a.spec;
    const t = m.stateT * (m.animRate || 1);       // tempo do clipe
    const bs = this.size;
    if (s.tipo === "corpo") {
      const [w0, w1] = s.janela || [0, a.length];
      // Golpe corpo a corpo: na preparação vira-se para o alvo e aproxima-se
      // se ele fugiu (antes começava no limite do alcance e o golpe caía no
      // vazio). Quando a janela do acerto começa, a mira fica presa: quem se
      // esquiva para o lado nessa hora escapa.
      if (p && s.virar !== false && t <= Number(s.mirar_ate || w0)) this.face(m, p.pos, cfg, dt, 1.0);
      if (p && s.perseguir !== false && t <= w0) {
        const d = Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / bs;
        const want = Number(s.perto || 1.1);
        if (d > want) {
          const sp = Number(cfg.run_speed || 3) * bs * Number(s.perseguir_vel || 1.0);
          this.walkTowards(m, p.pos, sp, dt, want * bs);
        }
      }
    } else if (p && s.virar !== false && t < Number(s.mirar_ate || 1e9)) {
      this.face(m, p.pos, cfg, dt, 0.5);
    }
    if (s.tipo === "corpo") {
      const [w0, w1] = s.janela || [0, a.length];
      if (t >= w0 && t <= w1) {
        const boxes = this.mobBoxes(m, s.caixas || [s.caixa || "espada"]);
        for (const [pid, pl] of players) {
          if (a.hit.has(pid)) continue;
          let done = false;
          for (const box of Object.values(boxes)) {
            for (const [part, pbox] of this.playerBoxes(pl)) {
              if (!boxesIntersect(box, pbox)) continue;
              // escudo só conta se estiver entre o mob e o jogador (uma lâmina
              // comprida que atravessa o corpo não "acerta" um escudo nas costas)
              if (part === "escudo" && Math.hypot(pbox.c[0] - m.x, pbox.c[2] - m.z)
                  >= Math.hypot(pl.pos[0] - m.x, pl.pos[2] - m.z)) continue;
              a.hit.add(pid);
              let damage = Number(s.dano || 2);
              if (part === "cabeca") damage *= HEAD_MULT;
              this.event(pid, { type: "player_hit", mob: m.id, tipo: m.tipo, damage, part,
                kind: s.dano_tipo || "espada", from: [m.x, m.y, m.z], magico: !!s.magico });
              done = true;
              break;
            }
            if (done) break;
          }
        }
      }
      const far = p ? Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / bs > 1.2 : false;
      if (s.avanco && far && t >= (s.janela || [0])[0] && t <= (s.janela || [0, 0])[1]) {
        const a2 = (m.yaw * Math.PI) / 180;
        this.move(m, Math.sin(a2) * s.avanco * bs * dt, Math.cos(a2) * s.avanco * bs * dt);
      }
    } else if (s.tipo === "area") {
      if (!a.fired.has("area") && t >= Number(s.em || 0)) {
        a.fired.add("area");
        const yr = (m.yaw * Math.PI) / 180;
        const fwd = Number(s.frente || 0) * bs;
        const cx = m.x + Math.sin(yr) * fwd, cz = m.z + Math.cos(yr) * fwd;
        const R = Number(s.raio || 2.5) * bs;
        for (const [pid, pl] of players) {
          const d = Math.hypot(pl.pos[0] - cx, pl.pos[2] - cz);
          const dy = pl.pos[1] - m.y;
          if (d <= R + Number(pl.half_width || 0.45) && dy > -bs * 1.5 && dy < bs * 3 + this.extent(m.tipo).hi) {
            const from = [cx, m.y + bs * 0.5, cz];
            this.event(pid, { type: "player_hit", mob: m.id, tipo: m.tipo, damage: Number(s.dano || 3),
              part: this.shieldCovers(pl, from) ? "escudo" : "corpo", kind: s.dano_tipo || "area",
              from, empurrao: Number(s.empurrao || 0), magico: !!s.magico });
          }
        }
        this.broadcast({ type: "mob_fx", fx: s.fx || "onda", pos: [cx, m.y + 0.2, cz], raio: R, tipo: m.tipo });
      }
    } else if (s.tipo === "projetil") {
      const times = Array.isArray(s.em) ? s.em : [Number(s.em || 0)];
      times.forEach((when, i) => {
        if (a.fired.has(i) || t < when) return;
        a.fired.add(i);
        if (p) this.fire(m, s, p);
      });
    } else if (s.tipo === "cura") {
      const per = Number(s.cura || 0.3) * Number(cfg.health || 20) / Math.max(a.length, 0.1);
      m.health = Math.min(Number(cfg.health || 20), m.health + per * dt);
      if (!a.fired.has("fx")) { a.fired.add("fx"); this.broadcast({ type: "mob_fx", fx: "cura", pos: [m.x, m.y + this.extent(m.tipo).hi, m.z], tipo: m.tipo }); }
    } else if (s.tipo === "aura") {
      if (!a.fired.has("aura") && t >= Number(s.em || 0)) {
        a.fired.add("aura");
        m.buffT = Number(s.dura || 10);
        this.broadcast({ type: "mob_fx", fx: "aura", pos: [m.x, m.y, m.z], tipo: m.tipo });
      }
    }
    if (t >= a.length) {
      m.action = null;
      this.setState(m, "idle");
    }
  }

  // Origem do projétil: o centro de uma caixa do modelo naquele instante
  // ("de": "orbe" = a esfera na mão da sombra; "olho" = o olho do anjo).
  fire(m, s, p) {
    const cfg = this.cfg(m);
    const spec = (cfg.projeteis || {})[s.projetil];
    if (!spec || this.projectiles.size >= MAX_PROJECTILES) return;
    const bs = this.size;
    let origin = [m.x, m.y + this.extent(m.tipo).hi * 0.7, m.z];
    if (s.de) {
      const b = this.mobBoxes(m, [s.de])[s.de];
      if (b) origin = b.c.slice();
    }
    const aim = [p.pos[0], p.pos[1] + Number(p.height || 3.0) * 0.55, p.pos[2]];
    const dir = norm3([aim[0] - origin[0], aim[1] - origin[1], aim[2] - origin[2]]);
    const pat = s.padrao || { tipo: "um" };
    const dirs = [];
    const yawOf = Math.atan2(dir[0], dir[2]);
    const pitch = Math.asin(Math.max(-1, Math.min(1, dir[1])));
    const fromAngles = (yw, pt) => [Math.sin(yw) * Math.cos(pt), Math.sin(pt), Math.cos(yw) * Math.cos(pt)];
    if (pat.tipo === "leque") {
      const n = Number(pat.n || 5), spread = (Number(pat.angulo || 40) * Math.PI) / 180;
      for (let i = 0; i < n; i++) dirs.push(fromAngles(yawOf + (n > 1 ? (i / (n - 1) - 0.5) * spread : 0), pitch));
    } else if (pat.tipo === "anel") {
      const n = Number(pat.n || 12);
      for (let i = 0; i < n; i++) dirs.push(fromAngles(yawOf + (i / n) * 2 * Math.PI, Number(pat.inclinacao || 0) * Math.PI / 180));
    } else {
      dirs.push(dir);
    }
    for (const d of dirs) {
      const id = `p${this.nextProj++}`;
      const speed = Number(spec.velocidade || 12) * bs;
      this.projectiles.set(id, {
        id, tipo: m.tipo, kind: s.projetil, owner: m.id, target: s.teleguiado === false ? null : m.target,
        pos: origin.slice(), vel: d.map((v) => v * speed), speed,
        life: Number(spec.vida || 4), damage: Number(spec.dano || 3), radius: Number(spec.raio || 0.3) * bs,
        homing: Number(spec.teleguiado || 0), gravity: Number(spec.gravidade || 0) * bs,
        magico: !!(spec.magico || s.magico),
      });
    }
  }

  // Projétil rebatido pela espada de um jogador: volta na direção para onde
  // ele olha (se olha mais ou menos para o mob que o atirou, vai teleguiado
  // para ele) e passa a acertar mobs em vez de jogadores.
  deflect(pid, projId, dir) {
    const pr = this.projectiles.get(String(projId));
    const pl = this.players.get(pid);
    if (!pr || !pl || pr.deflected) return { result: "invalido" };
    const bs = this.size;
    const c = [pl.pos[0], pl.pos[1] + Number(pl.height || 3.0) * 0.6, pl.pos[2]];
    if (Math.hypot(pr.pos[0] - c[0], pr.pos[1] - c[1], pr.pos[2] - c[2]) > DEFLECT_REACH * bs) {
      return { result: "invalido", motivo: "longe demais" };
    }
    let d;
    try {
      d = norm3(dir.map(Number));
      if (!d.every(Number.isFinite) || Math.hypot(...d) < 0.5) throw new Error();
    } catch (e) { return { result: "invalido" }; }
    const spec = ((this.cfg({ tipo: pr.tipo }).projeteis || {})[pr.kind]) || {};
    const mob = this.mobs.get(pr.owner);
    let homing = 0;
    if (mob && !mob.dead) {
      const toMob = norm3([mob.x - pr.pos[0], mob.y + this.extent(mob.tipo).hi * 0.5 - pr.pos[1], mob.z - pr.pos[2]]);
      if (toMob[0] * d[0] + toMob[1] * d[1] + toMob[2] * d[2] > 0.6) {
        d = toMob;
        homing = Math.max(pr.homing, 180);
      }
    }
    pr.deflected = true;
    pr.by = pid;
    pr.mobTarget = homing ? pr.owner : null;
    pr.homing = homing;
    pr.speed *= DEFLECT_SPEED;
    pr.vel = d.map((v) => v * pr.speed);
    pr.damage *= DEFLECT_DAMAGE;
    pr.life = Number(spec.vida || 4);
    pr.pos = c.map((v, i) => v + d[i] * 1.2 * bs);
    this.broadcast({ type: "mob_fx", fx: "rebatido", kind: pr.kind, tipo: pr.tipo, pos: pr.pos.slice() });
    return { result: "ok", damage: Number(spec.dano || 3) };
  }

  stepProjectiles(players, dt) {
    const bs = this.size;
    for (const pr of [...this.projectiles.values()]) {
      pr.life -= dt;
      if (pr.life <= 0) { this.projectiles.delete(pr.id); continue; }
      // teleguiado: vira um pouco para o alvo (graus por segundo)
      let want = null;
      if (pr.deflected) {
        const tm = pr.mobTarget ? this.mobs.get(pr.mobTarget) : null;
        if (tm && !tm.dead) want = norm3([tm.x - pr.pos[0], tm.y + this.extent(tm.tipo).hi * 0.5 - pr.pos[1], tm.z - pr.pos[2]]);
      } else {
        const tp = pr.target ? players.get(pr.target) : null;
        if (tp) want = norm3([tp.pos[0] - pr.pos[0], tp.pos[1] + Number(tp.height || 3.0) * 0.55 - pr.pos[1], tp.pos[2] - pr.pos[2]]);
      }
      if (pr.homing > 0 && want) {
        const cur = norm3(pr.vel);
        const dot = Math.max(-1, Math.min(1, cur[0] * want[0] + cur[1] * want[1] + cur[2] * want[2]));
        const ang = Math.acos(dot);
        const maxA = (pr.homing * Math.PI / 180) * dt;
        const f = ang > 1e-6 ? Math.min(1, maxA / ang) : 0;
        const nd = norm3(cur.map((v, i) => v + (want[i] - v) * f));
        pr.vel = nd.map((v) => v * pr.speed);
      }
      pr.vel[1] -= pr.gravity * dt;
      const from = pr.pos.slice();
      const to = pr.pos.map((v, i) => v + pr.vel[i] * dt);
      // jogadores: segmento contra as caixas (aumentadas pelo raio)
      let hit = false;
      // rebatido: acerta mobs (a caixa do corpo), não jogadores
      if (pr.deflected) {
        for (const m of this.mobs.values()) {
          if (m.dead || m.state === "morrendo") continue;
          const box = this.mobBoxes(m, ["corpo"]).corpo;
          if (!box || segmentBoxT(from, to, { ...box, h: box.h.map((v) => v + pr.radius) }) === null) continue;
          const r = this.attack(pr.by, m.id, pr.damage, from, "projetil");
          this.event(pr.by, { type: "attack_result", mob_id: m.id, ...r });
          hit = true;
          break;
        }
      }
      for (const [pid, pl] of (pr.deflected ? [] : players)) {
        // a primeira caixa em que o projétil entra (escudo, cabeça ou corpo)
        const order = this.playerBoxes(pl, pr.radius)
          .map(([part, box]) => [part, box, segmentBoxT(from, to, box)])
          .filter((e) => e[2] !== null).sort((a, b) => a[2] - b[2]);
        for (const [part] of order.slice(0, 1)) {
          this.event(pid, { type: "player_hit", mob: pr.owner, tipo: pr.tipo, part,
            damage: part === "cabeca" ? pr.damage * HEAD_MULT : pr.damage, kind: "projetil", from,
            magico: !!pr.magico });
          hit = true;
          break;
        }
        if (hit) break;
      }
      // blocos: anda em passos de 1/4 de bloco
      if (!hit) {
        const len = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
        const n = Math.max(1, Math.ceil(len / (bs * 0.25)));
        for (let k = 1; k <= n; k++) {
          const q = from.map((v, i) => v + (to[i] - v) * (k / n));
          const [cx, cy, cz] = this.cellOf(q[0], q[1], q[2]);
          if (this.world.solid(cx, cy, cz)) {
            hit = true;
            to.splice(0, 3, ...q);
            // O bloco leva o dano do projétil (a física da sala decide:
            // acumula, empurra ou quebra, como um golpe do jogador).
            if (this.onBlockHit) {
              const spec = ((this.cfg({ tipo: pr.tipo }).projeteis || {})[pr.kind]) || {};
              const dmg = pr.damage * Number(spec.dano_blocos ?? 1);
              const v = pr.vel, ax = [0, 1, 2].reduce((b, i) => (Math.abs(v[i]) > Math.abs(v[b]) ? i : b), 0);
              const dir = [0, 0, 0]; dir[ax] = Math.sign(v[ax]) || 1;
              if (dmg > 0) {
                try { this.onBlockHit([cx, cy, cz], dmg, dir); } catch (e) { console.warn("[mobs] bloco:", e.message); }
              }
            }
            break;
          }
        }
      }
      if (hit || to[1] < -60) {
        this.projectiles.delete(pr.id);
        this.broadcast({ type: "mob_fx", fx: "projetil", kind: pr.kind, tipo: pr.tipo, pos: to });
        continue;
      }
      pr.pos = to;
    }
  }

  playerBoxes(p, grow = 0) {
    const [x, y, z] = p.pos;
    const hw = Number(p.half_width || 0.45) + grow, h = Number(p.height || 3.0);
    const neck = y + h * (1 - HEAD_FRACTION);
    const out = [["cabeca", aabbBox([x - hw, neck, z - hw], [x + hw, y + h + grow, z + hw])],
                 ["corpo", aabbBox([x - hw, y - grow, z - hw], [x + hw, neck, z + hw])]];
    // escudo levantado: testado primeiro (o golpe que bate nele não chega ao corpo)
    const sh = this.shieldBox(p);
    if (sh) out.unshift(["escudo", grow ? { ...sh, h: sh.h.map((v) => v + grow) } : sh]);
    return out;
  }

  // Caixa do escudo levantado que o cliente mandou ("escudo": {c, a, h}),
  // conferida: números válidos, perto do jogador e de tamanho de escudo.
  shieldBox(p) {
    const b = p.escudo;
    if (!b || !Array.isArray(b.c) || !Array.isArray(b.a) || !Array.isArray(b.h)) return null;
    const ok = (v, n) => Array.isArray(v) && v.length === n && v.every(Number.isFinite);
    if (!ok(b.c, 3) || !ok(b.h, 3) || b.a.length !== 3 || !b.a.every((r) => ok(r, 3))) return null;
    if (b.h.some((v) => v < 0 || v > 1.5)) return null;
    const h = Number(p.height || 3.0);
    if (Math.hypot(b.c[0] - p.pos[0], b.c[2] - p.pos[2]) > 2.5) return null;
    if (b.c[1] < p.pos[1] - 1 || b.c[1] > p.pos[1] + h + 1) return null;
    const a = b.a.map((r) => { const n = Math.hypot(...r) || 1; return r.map((v) => v / n); });
    return { c: b.c.map(Number), a, h: b.h.map(Number) };
  }

  // Dano que vem de um ponto (explosão, onda): passa pelo escudo levantado
  // a caminho do peito do jogador? Então é o escudo que leva.
  shieldCovers(p, from) {
    const sh = this.shieldBox(p);
    if (!sh || !Array.isArray(from)) return false;
    const chest = [p.pos[0], p.pos[1] + Number(p.height || 3.0) * 0.5, p.pos[2]];
    return segmentBoxT(from, chest, sh) !== null;
  }

  shieldUp(m) {
    const block = this.cfg(m).block || {};
    if (!this.hasPart(m, block.parte || "escudo")) return false;
    if (m.state === "block") return true;
    return m.state === "block_start" && m.stateT >= Number(block.raised_after || 0.5);
  }

  // -- andar ---------------------------------------------------------------
  // Corpo a corpo: aproxima-se até ao alcance. À distância ("distancia":
  // [min, max]): aproxima-se, recua ou anda de lado a olhar para o alvo.
  positioning(m, p, tdist, dt) {
    const cfg = this.cfg(m);
    const bs = this.size;
    const [dmin, dmax] = cfg.distancia || [0, this.meleeReach(m) * 0.85];
    this.face(m, p.pos, cfg, dt);
    const fly = m.flying;
    if (tdist > dmax) {
      const run = tdist > Number(cfg.run_range ?? 5);
      const speed = Number(cfg[run ? "run_speed" : "walk_speed"] || 2) * bs;
      this.setState(m, "chase", fly ? "fly_forward" : run ? "run" : "walk");
      this.walkTowards(m, p.pos, speed, dt, dmax * 0.9 * bs);
    } else if (tdist < dmin) {
      const speed = Number(cfg.walk_speed || 2) * bs;
      const dx = m.x - p.pos[0], dz = m.z - p.pos[2];
      const n = Math.hypot(dx, dz) || 1;
      this.setState(m, "recuar", fly ? "fly_forward" : "walk");
      if (!this.move(m, (dx / n) * speed * dt, (dz / n) * speed * dt)) this.strafe(m, p, speed, dt);
    } else if (dmin > 0) {
      // à distância certa: anda de lado de vez em quando
      m.strafeT -= dt;
      if (m.strafeT <= 0) { m.strafe = Math.random() < 0.5 ? 1 : -1; m.strafeT = rand(1.5, 3.5); m.strafeOn = Math.random() < 0.6; }
      if (m.strafeOn) {
        this.setState(m, "rodear", fly ? "fly_forward" : "walk");
        this.strafe(m, p, Number(cfg.walk_speed || 2) * bs * 0.7, dt);
      } else {
        this.setState(m, "idle", fly ? "fly_idle" : "idle");
      }
    } else {
      this.setState(m, "idle", fly ? "fly_idle" : "idle");
    }
  }

  // Até onde chegam os golpes corpo a corpo que o mob pode dar agora (com as
  // partes que tem): é até aí que se aproxima.
  meleeReach(m) {
    const cfg = this.cfg(m);
    const missing = this.missingParts(m);
    const ready = [], all = [];
    for (const [name, a] of Object.entries(cfg.ataques || {})) {
      if (a.tipo !== "corpo" && a.tipo !== "area") continue;
      if ((a.requer || []).some((x) => missing.includes(x))) continue;
      if ((a.sem || []).some((x) => !missing.includes(x))) continue;
      if (a.voando === false && m.flying) continue;
      const r = Number((a.alcance || [0, 1.6])[1]);
      all.push(r);
      if ((m.cds[name] || 0) <= 0) ready.push(r);
    }
    // um golpe pronto: chega ao alcance do maior; nenhum pronto: fica perto
    if (ready.length) return Math.max(...ready);
    if (all.length) return Math.min(...all);
    return Number(cfg.attack_range || 1.6);
  }

  strafe(m, p, speed, dt) {
    const dx = p.pos[0] - m.x, dz = p.pos[2] - m.z;
    const n = Math.hypot(dx, dz) || 1;
    const sx = (-dz / n) * m.strafe, sz = (dx / n) * m.strafe;
    if (!this.move(m, sx * speed * dt, sz * speed * dt)) m.strafe = -m.strafe;
  }

  // -- fome e sede (sala pública) -------------------------------------------
  // Devolve true enquanto o mob está ocupado a ir comer/beber (ou a comer).
  needs(m, cfg, dt) {
    if (!cfg.passivo || m.flying) return false;
    m.hunger = Math.min(1.5, m.hunger + dt / HUNGER_TIME);
    m.thirst = Math.min(1.5, m.thirst + dt / THIRST_TIME);
    const s = this.size;
    if (!m.need) {
      if (m.hunger < NEED_AT && m.thirst < NEED_AT) return false;
      m.needSearchT -= dt;
      if (m.needSearchT > 0) return false;
      m.needSearchT = NEED_SEARCH_EVERY;
      const order = m.thirst >= m.hunger ? ["beber", "comer"] : ["comer", "beber"];
      for (const kind of order) {
        if ((kind === "beber" ? m.thirst : m.hunger) < NEED_AT) continue;
        const cell = kind === "beber" ? this.nearestWater(m) : this.nearestTuft(m);
        if (cell) { m.need = { kind, cell, t: 0, eat: 0 }; m.goal = null; break; }
      }
      if (!m.need) return false;
    }
    const n = m.need;
    n.t += dt;
    const pos = [n.cell[0] * s, m.y, n.cell[2] * s];
    const reach = (n.kind === "beber" ? 1.4 : 0.8) * s;
    const dist = Math.hypot(pos[0] - m.x, pos[2] - m.z);
    if (n.kind === "comer" && this.world.get(n.cell[0], n.cell[1], n.cell[2]) !== GRASS_TUFT) {
      m.need = null;                       // outro comeu-o / partiram-no
      return false;
    }
    if (dist > reach) {
      if (n.t > NEED_GIVE_UP) { m.need = null; m.needSearchT = 6; return false; }
      this.face(m, pos, cfg, dt);
      this.setState(m, "wander", "walk");
      if (!this.walkTowards(m, pos, Number(cfg.walk_speed || 2) * s * 0.8, dt, reach * 0.8)) {
        m.need = null; m.needSearchT = 6;  // caminho tapado
        return false;
      }
      return true;
    }
    if (n.kind === "beber" && !((this.waterMap && this.waterMap().get(n.cell.join(","))) > 0)) {
      m.need = null;                       // a água acabou ali
      return false;
    }
    // chegou: come / bebe (animações "comendo" / "bebendo")
    this.face(m, pos, cfg, dt);
    const anims = cfg.animations || {};
    const want = n.kind === "beber" ? "bebendo" : "comendo";
    const clip = anims[want] ? want : (anims.idle_raro ? "idle_raro" : "idle");
    if (m.state !== "idle" || m.role !== clip) this.setState(m, "idle", clip);
    n.eat += dt;
    if (n.eat >= EAT_TIME) {
      if (n.kind === "comer") {
        m.hunger = 0;
        if (this.onGraze) {
          try { this.onGraze(n.cell); } catch (e) { console.warn("[mobs] capim:", e.message); }
        }
      } else {
        m.thirst = 0;
        this.drinks.push(n.cell);          // a sala tira 1/8 dessa água (drainDrinks)
      }
      m.need = null;
      this.setState(m, "idle", "idle");
    }
    return true;
  }

  nearestTuft(m) {
    const s = this.size, R = NEED_RADIUS;
    const [cx, cy, cz] = this.cellOf(m.x, m.y + s * 0.1, m.z);
    let best = null, bd = Infinity;
    for (let dx = -R; dx <= R; dx++) {
      for (let dz = -R; dz <= R; dz++) {
        const d = dx * dx + dz * dz;
        if (d > R * R || d >= bd) continue;
        for (const dy of [0, -1, 1]) {
          if (this.world.get(cx + dx, cy + dy, cz + dz) === GRASS_TUFT) {
            best = [cx + dx, cy + dy, cz + dz]; bd = d; break;
          }
        }
      }
    }
    return best;
  }

  nearestWater(m) {
    const water = this.waterMap ? this.waterMap() : null;
    if (!water || !water.size) return null;
    const s = this.size, R = NEED_RADIUS;
    const [cx, cy, cz] = this.cellOf(m.x, m.y, m.z);
    let best = null, bd = Infinity;
    for (const [k, lv] of water) {
      if (lv < 1) continue;
      const [x, y, z] = k.split(",").map(Number);
      if (Math.abs(y - cy) > 3) continue;
      const d = (x - cx) ** 2 + (z - cz) ** 2;
      if (d <= R * R && d < bd) { best = [x, y, z]; bd = d; }
    }
    return best;
  }

  wander(m, cfg, dt) {
    if (this.needs(m, cfg, dt)) return;
    const bs = this.size;
    const fly = m.flying;
    if (!m.goal) {
      m.think -= dt;
      if (m.state !== "idle") this.setState(m, "idle", fly ? "fly_idle" : "idle");
      // de vez em quando uma animação "rara" parada (o anjo gira os anéis)
      const rare = (cfg.animations || {}).idle_raro;
      if (rare && m.state === "idle" && m.role !== "idle_raro" && Math.random() < 0.02 * dt) this.setState(m, "idle", "idle_raro");
      if (m.think > 0) return;
      const r = Number(cfg.wander_radius || 6) * bs;
      const a = rand(0, 2 * Math.PI), d = rand(0.3, 1) * r;
      m.goal = [m.home[0] + Math.cos(a) * d, m.home[2] + Math.sin(a) * d];
      // "pensar" (mobs.json): quanto tempo pára entre passeios (fadas: quase nada)
      const pen = Array.isArray(cfg.pensar) ? cfg.pensar.map(Number) : [2, 5];
      m.think = rand(pen[0], pen[1]);
    }
    const [gx, gz] = m.goal;
    if (Math.hypot(gx - m.x, gz - m.z) < bs * 0.3 || m.stateT > 8) {
      m.goal = null;
      this.setState(m, "idle", fly ? "fly_idle" : "idle");
      return;
    }
    this.face(m, [gx, m.y, gz], cfg, dt);
    this.setState(m, "wander", fly ? "fly_forward" : "walk");
    if (!this.walkTowards(m, [gx, m.y, gz], Number(cfg.walk_speed || 2) * bs * 0.7, dt)) m.goal = null;
  }

  face(m, pos, cfg, dt, mult = 1) {
    const want = (Math.atan2(pos[0] - m.x, pos[2] - m.z) * 180) / Math.PI;
    const diff = angDiff(want, m.yaw);
    const step = Number(cfg.turn_speed || 300) * dt * mult;
    m.yaw = (((m.yaw + Math.max(-step, Math.min(step, diff))) % 360) + 360) % 360;
  }

  facing(m, pos) {
    const want = (Math.atan2(pos[0] - m.x, pos[2] - m.z) * 180) / Math.PI;
    return Math.abs(angDiff(want, m.yaw));
  }

  // -- movimento ----------------------------------------------------------
  cellOf(x, y, z) {
    const s = this.size;
    return [Math.floor((x + s / 2) / s), Math.floor(y / s), Math.floor((z + s / 2) / s)];
  }

  radius(m) { return this.extent(m.tipo).r; }

  blocked(m, x, y, z) {
    const s = this.size;
    const e = this.extent(m.tipo);
    const r = e.r;
    const y0 = y + Math.max(0.05, e.lo), y1 = y + e.hi - 0.05;
    const [cx0, , cz0] = this.cellOf(x - r, y0, z - r);
    const [cx1, , cz1] = this.cellOf(x + r, y0, z + r);
    const by0 = Math.floor(y0 / s), by1 = Math.floor(y1 / s);
    for (let cx = cx0; cx <= cx1; cx++)
      for (let cz = cz0; cz <= cz1; cz++)
        for (let cy = by0; cy <= by1; cy++)
          if (this.world.solid(cx, cy, cz)) return true;
    return false;
  }

  move(m, dx, dz) {
    let moved = false;
    for (const [ddx, ddz] of [[dx, dz], [dx, 0], [0, dz]]) {
      if (ddx === 0 && ddz === 0) continue;
      const nx = m.x + ddx, nz = m.z + ddz;
      if (!this.blocked(m, nx, m.y, nz)) { m.x = nx; m.z = nz; moved = true; break; }
      const up = m.y + this.size;
      if (!m.flying && m.vy === 0 && !this.blocked(m, nx, up, nz) && !this.blocked(m, m.x, up, m.z)) {
        m.x = nx; m.y = up; m.z = nz; moved = true; break;
      }
      if (m.flying && !this.blocked(m, m.x, m.y + this.size * 0.5, m.z)) {
        m.y += this.size * 0.5; moved = true; break;       // voando: sobe por cima
      }
    }
    if (moved) this.dirty = true;
    return moved;
  }

  walkTowards(m, pos, speed, dt, stopAt = 0) {
    const dx = pos[0] - m.x, dz = pos[2] - m.z;
    const d = Math.hypot(dx, dz);
    if (d <= stopAt || d < 1e-6) return true;
    const step = Math.min(speed * dt, d - stopAt);
    return this.move(m, (dx / d) * step, (dz / d) * step);
  }

  ground(m) {
    const s = this.size, r = this.radius(m);
    let best = -1e9;
    for (const [ox, oz] of [[0, 0], [-r, -r], [r, -r], [-r, r], [r, r]]) {
      const [cx, cy, cz] = this.cellOf(m.x + ox, m.y - 0.01, m.z + oz);
      for (let y = cy; y > cy - 24; y--) {
        if (this.world.solid(cx, y, cz)) { best = Math.max(best, (y + 1) * s); break; }
      }
    }
    return best;
  }

  onGround(m) { return !m.flying && m.vy === 0 && m.y <= this.ground(m) + 1e-3; }

  // Gravidade, voo e a aterragem.
  physics(m, players, dt) {
    const cfg = this.cfg(m);
    const voo = cfg.voo;
    const g = this.ground(m);
    const s = this.size;
    if (voo && !voo.sempre && !m.dead) {
      // a sombra levanta voo quando o alvo está bem acima e pousa quando não
      // Voa quando o alvo está bem acima ou longe ("longe", em blocos: ataca à
      // distância lá de cima) e desce para lutar corpo a corpo quando perto.
      const p = m.target ? players.get(m.target) : null;
      const above = p ? (p.pos[1] - m.y) / s : 0;
      const dist = p ? Math.hypot(p.pos[0] - m.x, p.pos[2] - m.z) / s : 0;
      const longe = Number(voo.longe || 1e9);
      const wantsUp = p && (above >= Number(voo.alvo_acima || 3) || dist > longe);
      const staysUp = p && ((p.pos[1] - g) / s >= 1.5 || dist > longe - 1.5);
      if (!m.flying && wantsUp && !m.action) {
        m.flying = true; m.vy = 0;
        this.setState(m, "idle", "fly_idle");
      } else if (m.flying && !staysUp && !m.action) {
        m.flying = false;            // cai até ao chão (aterragem com fall_landing)
      }
    }
    if (m.flying) {
      const p = m.target ? players.get(m.target) : null;
      const alt = Number((voo || {}).altura || 1) * s;
      const bob = Math.sin(this.clock * 2 + m.x) * Number((voo || {}).bob || 0) * s;
      let want = g + alt + bob;
      if (p && (voo || {}).altura_alvo !== undefined) want = p.pos[1] + Number(voo.altura_alvo) * s + bob * 0.3;
      else if (p) want = Math.max(want, p.pos[1] + alt * 0.5 + bob);
      const dy = want - m.y;
      const vs = Number((voo || {}).subida || 4) * s * dt;
      const ny = m.y + Math.max(-vs, Math.min(vs, dy));
      if (!this.blocked(m, m.x, ny, m.z)) m.y = ny;
      if (m.y < g) m.y = g;
      m.vy = 0;
      m.fallTop = m.y;
    } else if (m.y > g + 1e-3 || m.vy > 0) {
      m.vy -= GRAVITY * dt;
      const ny = m.y + m.vy * dt;
      if (m.vy > 0 && this.blocked(m, m.x, ny, m.z)) m.vy = 0;
      else m.y = ny;
      m.fallTop = Math.max(m.fallTop, m.y);
      if (m.y <= g) {
        m.y = g; m.vy = 0;
        const fell = (m.fallTop - g) / s;
        m.fallTop = m.y;
        // aterragem pesada (sombra): animação de pousar
        if (fell >= 2.5 && (cfg.animations || {}).fall_landing && !m.action && !m.dead) {
          this.playClip(m, "pousar", this.clipFor(m, "fall_landing"), Number(cfg.pousar_inicio || 0));
        }
      }
      this.dirty = true;
    } else {
      m.y = Math.max(m.y, g);
      m.vy = 0;
      m.fallTop = m.y;
    }
    if (m.y < -50) {
      m.dead = true;
      this.mobs.delete(m.id);
      this.broadcast({ type: "mob_died", mob: m.id, tipo: m.tipo, pos: [m.x, m.y, m.z] });
    }
  }

  separate() {
    const mobs = [...this.mobs.values()].filter((m) => !m.dead);
    for (let i = 0; i < mobs.length; i++) {
      const a = mobs[i], ra = this.radius(a);
      for (let j = i + 1; j < mobs.length; j++) {
        const b = mobs[j], rb = this.radius(b);
        const dx = b.x - a.x, dz = b.z - a.z;
        const d = Math.hypot(dx, dz);
        if (d < ra + rb && Math.abs(a.y - b.y) < this.size) {
          const push = (ra + rb - d) / 2;
          const [nx, nz] = d > 1e-6 ? [dx / d, dz / d] : [1, 0];
          this.move(a, -nx * push, -nz * push);
          this.move(b, nx * push, nz * push);
        }
      }
    }
  }

  // -- aparecimento -------------------------------------------------------
  // Mobs gravados acima do máximo de agora (ex.: anjos de quando eram 3):
  // os que sobram somem ao abrir a sala.
  // Regras de spawn do tipo nesta zona: mobs.json "spawn": {"publica": {...},
  // "dungeon": {...}, "privada": {...}}. Campos: max (ou por_andar na
  // dungeon), encher (começa já com a população cheia), recarga (s entre
  // nascimentos depois que o número baixa), espalhar (pela sala toda em vez
  // de perto dos jogadores), min_distance/max_distance, andares (dungeon: só
  // nestes andares, contados de baixo, onde se entra: [1] = o 1º).
  floorsFor(sp) {
    const all = (this.andares || []).map((_, i) => i);
    if (!Array.isArray(sp.andares) || !sp.andares.length) return all;
    return all.filter((i) => sp.andares.map(Number).includes(i + 1));
  }

  // -- berços (mobs.json "ninho": {bloco, inicial:[a,b], max, recarga}) ----
  // As fadas nascem dos berços solares, em qualquer sala e onde quer que o
  // bloco esteja (gerado com o mundo ou posto por um jogador). Cada berço
  // solta inicial (1-3) logo que aparece e depois mais uma a cada "recarga"
  // s até ter "max". Se o berço some (apanhado/partido), as fadas dele somem.
  nests(dt) {
    this.nestT = (this.nestT || 0) - dt;
    if (this.nestT > 0) return;
    const step = 1.0;
    this.nestT = step;
    const types = Object.entries(mobTypes()).filter(([, c]) => c && c.ninho);
    if (!types.length) return;
    if (!this.nestState) this.nestState = new Map();     // "tipo|x,y,z" -> {t}
    const s = this.size;
    for (const [tipo, cfg] of types) {
      const nest = cfg.ninho;
      const want = String(nest.bloco);
      const cells = new Set();
      for (const src of [this.world.plants || new Map(), this.world.mods]) {
        for (const k of src.keys()) {
          if (cells.has(k)) continue;
          const [x, y, z] = k.split(",").map(Number);
          if (this.world.get(x, y, z) === want) cells.add(k);
        }
      }
      // fadas por berço; as de berços que já não existem somem
      const byNest = new Map();
      for (const m of [...this.mobs.values()]) {
        if (m.tipo !== tipo) continue;
        if (!m.nest || !cells.has(m.nest)) {
          this.broadcast({ type: "mob_died", mob: m.id, tipo: m.tipo, pos: [m.x, m.y, m.z] });
          this.mobs.delete(m.id); this.dirty = true;
          continue;
        }
        if (!m.dead) byNest.set(m.nest, (byNest.get(m.nest) || 0) + 1);
      }
      const max = Number(nest.max || 3);
      const cap = Number(nest.max_sala || 120);
      let total = [...byNest.values()].reduce((a, b) => a + b, 0);
      for (const k of cells) {
        const sk = `${tipo}|${k}`;
        let st = this.nestState.get(sk);
        const have = byNest.get(k) || 0;
        let n = 0;
        if (!st) {
          st = { t: Number(nest.recarga || 20) };
          this.nestState.set(sk, st);
          const [a, b] = (nest.inicial || [1, 3]).map(Number);
          n = Math.max(0, Math.floor(rand(a, b + 1)) - have);   // solta 1-3 logo
        } else if (have < max) {
          st.t -= step;
          if (st.t <= 0) { n = 1; st.t = Number(nest.recarga || 20); }
        } else {
          st.t = Number(nest.recarga || 20);
        }
        n = Math.min(n, max - have, cap - total);
        const [x, y, z] = k.split(",").map(Number);
        for (let i = 0; i < n; i++) {
          const pos = [x * s + rand(-0.3, 0.3) * s, (y + 0.5) * s, z * s + rand(-0.3, 0.3) * s];
          const r = this.spawn(tipo, pos);
          if (r.result !== "ok") break;
          const m = this.mobs.get(r.mob.id);
          m.nest = k;
          m.home = [x * s, (y + 0.5) * s, z * s];
          total++;
        }
      }
      for (const sk of [...this.nestState.keys()]) {
        if (sk.startsWith(tipo + "|") && !cells.has(sk.slice(tipo.length + 1))) this.nestState.delete(sk);
      }
    }
  }

  spawnSpec(tipo) {
    // ajustes dos moderadores (painel de administração): desligar a sala ou
    // um tipo, mudar o máximo/recarga, ou pôr um tipo que não nascia aqui
    const adm = this.admin || null;
    if (adm && adm.desligado) return null;
    const at = adm && adm.tipos ? adm.tipos[tipo] : null;
    if (at && at.desligado) return null;
    if ((mobTypes()[tipo] || {}).ninho) return null;      // nascem dos berços (nests)
    const sp = (mobTypes()[tipo] || {}).spawn || {};
    let z = sp[this.zona];
    if (!z && at && at.max > 0) z = { max: at.max, recarga: at.recarga || 30, espalhar: true };
    if (!z) return null;
    const out = { ...z };
    if (at && at.max !== undefined) { delete out.por_andar; out.max = at.max; }
    if (at && at.recarga !== undefined) out.recarga = at.recarga;
    if (this.zona === "dungeon" && out.por_andar !== undefined) {
      out.max = Number(out.por_andar) * (this.andares ? this.floorsFor(out).length : 1);
    }
    out.max = Number(out.max || 0);
    return out.max > 0 ? out : null;
  }

  trimToMax() {
    for (const tipo of Object.keys(mobTypes())) {
      const sp = this.spawnSpec(tipo);
      const max = sp ? sp.max : 0;
      const list = [...this.mobs.values()].filter((m) => m.tipo === tipo);
      for (const m of list.slice(max)) { this.mobs.delete(m.id); this.dirty = true; }
    }
  }

  // Ao abrir (com "encher") nasce a população toda; depois só nasce quando
  // o número baixa, um de cada vez, com "recarga" s entre um e outro.
  spawner(dt) {
    for (const tipo of Object.keys(mobTypes())) {
      const sp = this.spawnSpec(tipo);
      if (!sp) continue;
      const alive = [...this.mobs.values()].filter((m) => m.tipo === tipo && !m.dead).length;
      const recarga = Number(sp.recarga || sp.interval || 30);
      if (alive >= sp.max) {
        this.filled[tipo] = true;
        this.spawnT[tipo] = recarga;           // a recarga conta a partir de quando baixa
        continue;
      }
      if (sp.encher && !this.filled[tipo]) {
        for (let k = alive, tries = 0; k < sp.max && tries < sp.max * 3; tries++) {
          if (this.spawn(tipo).result === "ok") k++;
        }
        this.filled[tipo] = true;
        this.spawnT[tipo] = recarga;
        continue;
      }
      const t = (this.spawnT[tipo] ?? recarga * 0.2) - dt;
      this.spawnT[tipo] = t;
      if (t > 0) continue;
      this.spawnT[tipo] = recarga;
      this.spawn(tipo);
    }
  }

  // Lugar livre: chão sólido e espaço para o mob em cima.
  freeSpot(probe, cx, cy, cz) {
    const s = this.size;
    if (!this.world.solid(cx, cy, cz)) return null;
    const pos = [cx * s, (cy + 1) * s, cz * s];
    return this.blocked(probe, pos[0], pos[1], pos[2]) ? null : pos;
  }

  spawnPoint(tipo) {
    const sp = this.spawnSpec(tipo) || {};
    const s = this.size;
    const probe = { tipo };
    const R = this.room;
    // dungeon: num andar com menos mobs deste tipo, num sítio livre do labirinto
    if (this.zona === "dungeon" && this.andares && R) {
      const count = this.andares.map(() => 0);
      for (const m of this.mobs.values()) {
        if (m.tipo !== tipo || m.dead) continue;
        const cy = Math.floor(m.y / s) - 1;
        let k = 0;
        for (let i = 0; i < this.andares.length; i++) if (this.andares[i] <= cy) k = i;
        count[k]++;
      }
      const allowed = new Set(this.floorsFor(sp));
      const order = count.map((c, i) => [c + Math.random() * 0.5, i])
        .filter(([, i]) => allowed.has(i)).sort((a, b) => a[0] - b[0]);
      for (const [, i] of order) {
        const fy = this.andares[i];
        for (let t = 0; t < 40; t++) {
          const cx = Math.floor(rand(3, R.size_x - 3)), cz = Math.floor(rand(3, R.size_z - 3));
          const pos = this.freeSpot(probe, cx, fy, cz);
          if (pos) return pos;
        }
      }
      return null;
    }
    // de_bloco: nasce num bloco desse tipo (as fadas saem dos mini sóis)
    if (sp.de_bloco) {
      const want = String(sp.de_bloco);
      // mini sóis da criação (world.plants) e os que nasceram depois (mods);
      // confere com world.get (o valor gravado pode ser {type, ...})
      const cells = [];
      const seen = new Set();
      for (const src of [this.world.plants || new Map(), this.world.mods]) {
        for (const k of src.keys()) {
          if (seen.has(k)) continue;
          seen.add(k);
          const [x, y, z] = k.split(",").map(Number);
          if (this.world.get(x, y, z) === want) cells.push(k);
        }
      }
      for (let t = 0; t < 6 && cells.length; t++) {
        const [x, y, z] = cells[Math.floor(Math.random() * cells.length)].split(",").map(Number);
        const pos = [x * s, (y + 0.5) * s, z * s];
        if (!this.blocked(probe, pos[0], pos[1], pos[2])) return pos;
      }
      return null;
    }
    // espalhar: qualquer lugar da sala (do topo para baixo até achar chão)
    if (sp.espalhar && R) {
      for (let t = 0; t < 40; t++) {
        const cx = Math.floor(rand(2, R.size_x - 2)), cz = Math.floor(rand(2, R.size_z - 2));
        for (let cy = Math.min(this.world.size_y - 3, Number(R.size_y || 20) + 4); cy >= 0; cy--) {
          if (this.world.solid(cx, cy, cz)) {
            const pos = this.freeSpot(probe, cx, cy, cz);
            if (pos) return pos;
            break;
          }
        }
      }
      return null;
    }
    const players = [...this.activePlayers().values()];
    if (!players.length) return null;
    for (let i = 0; i < 20; i++) {
      const p = players[Math.floor(Math.random() * players.length)].pos;
      const a = rand(0, 2 * Math.PI);
      const d = rand(Number(sp.min_distance || 10), Number(sp.max_distance || 20)) * s;
      const x = p[0] + Math.cos(a) * d, z = p[2] + Math.sin(a) * d;
      const [cx, , cz] = this.cellOf(x, 0, z);
      for (let cy = Math.min(this.world.size_y - 3, Math.floor(p[1] / s) + 6); cy >= 0; cy--) {
        if (this.world.solid(cx, cy, cz)) {
          const pos = [cx * s, (cy + 1) * s, cz * s];
          if (!this.blocked(probe, pos[0], pos[1], pos[2])) return pos;
          break;
        }
      }
    }
    return null;
  }
}

module.exports = { MobServer, TICK, mobTypes, HEAD_MULT };
