// Economia: o que cada jogador TEM é decidido aqui no servidor.
//
// O livro de cada jogador vive no processo Python (server/physics/economy.py,
// dentro do worker da física, que já decide pôr/tirar blocos). Este módulo:
//   * carrega o livro ao entrar (login/registo/hello) e dá os itens iniciais;
//   * grava no registo do jogador o que o Python manda ("eco_save");
//   * guarda/apaga o conteúdo dos baús levados no inventário ("icont_*");
//   * credita drops e almas de mobs; serve as configurações ao jogo.
//
// Campos do jogador que SÓ o servidor escreve: propertie_inventory, almas,
// economia, itens_iniciais, almas_iniciais e properties_status (menos a vida).
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const db = require("./db");
const config = require("./config");
const physics = require("./physics/bridge");

// ---------------------------------------------------------------- economia.json
let ECO = null;
let ECO_MTIME = 0;
function eco() {
  const file = path.join(config.dataDir, "economia.json");
  try {
    const m = fs.statSync(file).mtimeMs;
    if (!ECO || m !== ECO_MTIME) {
      ECO = JSON.parse(fs.readFileSync(file, "utf8"));
      ECO_MTIME = m;
    }
  } catch (e) {
    if (!ECO) {
      console.warn("[economia] economia.json ilegível:", e.message);
      ECO = {};
    }
  }
  return ECO;
}

function testMode() {
  const env = process.env.MODO_TESTES;
  if (env !== undefined && env !== "") return env === "1" || env.toLowerCase() === "true";
  return !!eco().testes;
}

// ---------------------------------------------------------------- configurações
// Os ficheiros que definem o jogo: vão para o jogo ao entrar (o jogo não usa
// cópias locais online, por isso mexer nelas não muda nada).
const CONFIG_FILES = ["itens.json", "item_balancing.json", "blocks.json", "mobs.json",
                      "receitas.json", "combate.json"];
let bundleCache = null;
let bundleKey = "";
let OVERRIDES = {};             // ficheiros mudados no painel (MongoDB), valem mais

function setOverrides(ov) {
  OVERRIDES = { ...(ov || {}) };
  bundleCache = null;
}

function configBundle() {
  const stamps = CONFIG_FILES.map((f) => {
    try { return fs.statSync(path.join(config.dataDir, f)).mtimeMs; } catch (e) { return 0; }
  }).join("|");
  if (bundleCache && stamps === bundleKey) return bundleCache;
  const files = {};
  for (const f of CONFIG_FILES) {
    if (OVERRIDES[f]) { files[f] = OVERRIDES[f]; continue; }
    try {
      files[f] = JSON.parse(fs.readFileSync(path.join(config.dataDir, f), "utf8"));
    } catch (e) {
      console.warn(`[config] ${f}:`, e.message);
    }
  }
  const versao = crypto.createHash("sha1").update(JSON.stringify(files)).digest("hex").slice(0, 12);
  bundleCache = { versao, testes: testMode(), files };
  bundleKey = stamps;
  return bundleCache;
}

// ---------------------------------------------------------------- status
// Status base iguais para todos (economia.json); do jogo só se aceita a vida.
function cleanStatus(stored, fromGame, almas) {
  const base = { vida_max: "10", attack_speed: "2", velocidade: "10", range_ataque: "3",
                 forca: "600", armadura: "0", peso: "40", ...(eco().status_base || {}) };
  const out = { ...base };
  const vidaPts = ((almas && almas.pontos) || []).filter((p) => p === "vida").length;
  const max = (parseFloat(base.vida_max) || 10) + vidaPts;
  let vida = parseFloat(String((fromGame && fromGame.vida) ?? (stored && stored.vida) ?? max).replace(",", "."));
  if (!Number.isFinite(vida)) vida = max;
  out.vida = String(Math.round(Math.max(0, Math.min(max, vida)) * 100) / 100);
  return out;
}

// ---------------------------------------------------------------- gravar
const SAVE_DELAY = 1000;
const latest = new Map();       // pid -> {ledger, inventory, almas} (o mais recente do Python)
const timers = new Map();
const loadedGen = new Map();    // pid -> geração do Python onde o livro foi carregado

function onSave(ev) {
  const pid = String(ev.player);
  latest.set(pid, { ledger: ev.ledger, inventory: ev.inventory, almas: ev.almas });
  if (timers.has(pid)) return;
  timers.set(pid, setTimeout(() => flush(pid).catch((e) => console.warn("[economia]", e.message)),
                             SAVE_DELAY));
}

async function flush(pid) {
  clearTimeout(timers.get(pid));
  timers.delete(pid);
  const s = latest.get(pid);
  if (!s) return;
  await db.savePlayer(pid, { economia: s.ledger, propertie_inventory: s.inventory, almas: s.almas });
}

// ---------------------------------------------------------------- carregar
function starterGrants(player) {
  const cfg = eco();
  const got = new Set((player.itens_iniciais || []).map(String));
  const items = [];
  for (const row of cfg.itens_iniciais || []) {
    if (!Array.isArray(row) || got.has(String(row[0]))) continue;
    items.push([String(row[0]), Math.max(1, parseInt(row[1], 10) || 1)]);
  }
  const souls = player.almas_iniciais ? 0 : Math.max(0, parseInt(cfg.almas_iniciais, 10) || 0);
  return { items, souls };
}

// Carrega o livro no Python (se ainda não está nesta geração do processo) e
// devolve {inventory, almas} para o jogo. ``grant``: dá os itens iniciais.
async function load(pid, { grant = false } = {}) {
  pid = String(pid);
  const player = await db.getPlayer(pid);
  if (!player) throw new Error("jogador não existe");
  const g = grant ? starterGrants(player) : { items: [], souls: 0 };
  const mem = latest.get(pid);
  const r = await physics.request({
    op: "eco_load", player: pid,
    ledger: (mem && mem.ledger) || player.economia || null,
    inventory: (mem && mem.inventory) || player.propertie_inventory || null,
    almas: (mem && mem.almas) || player.almas || null,
    grants: g.items, souls: g.souls,
  });
  if (r.erro) throw new Error(r.erro);
  loadedGen.set(pid, physics.generation());
  const set = {};
  if (g.items.length) {
    set.itens_iniciais = [...(player.itens_iniciais || []), ...g.items.map((x) => x[0])];
    console.log(`[economia] ${pid}: itens iniciais ${g.items.map((x) => x.join(" x")).join(", ")}`);
  }
  if (g.souls) set.almas_iniciais = true;
  set.properties_status = cleanStatus(player.properties_status, null, r.almas);
  await db.savePlayer(pid, set);
  return { inventory: r.inventory, almas: r.almas, properties_status: set.properties_status };
}

// O livro está no processo Python atual? (ele pode ter reiniciado)
async function ensure(pid) {
  if (loadedGen.get(String(pid)) === physics.generation()) return;
  await load(pid);
}

// Python reiniciou: recarrega os livros dos jogadores ligados.
async function reloadAll(pids) {
  for (const pid of pids) {
    try { await load(pid); } catch (e) { console.warn("[economia] recarregar", pid, e.message); }
  }
}

// ---------------------------------------------------------------- baús no inventário
const CID = /^cnt-[a-f0-9]{8,32}$/;

async function containerEntries(cid) {
  if (!CID.test(String(cid || ""))) return [];
  const doc = await db.db.collection("containers").findOne({ _id: String(cid) });
  return (doc && Array.isArray(doc.slots)) ? doc.slots : [];
}

async function containerExists(cid) {
  if (!CID.test(String(cid || ""))) return true;
  return !!(await db.db.collection("containers").findOne({ _id: String(cid) }, { projection: { _id: 1 } }));
}

async function storeContainer(cid, entries, by) {
  if (!CID.test(String(cid || ""))) return;
  await db.db.collection("containers").updateOne({ _id: String(cid) },
    { $set: { slots: (entries || []).slice(0, 64), updated: new Date(), by } }, { upsert: true });
}

async function deleteContainer(cid) {
  if (!CID.test(String(cid || ""))) return;
  await db.db.collection("containers").deleteOne({ _id: String(cid) });
}

// Fechou um baú que leva no inventário: confere com o livro e grava.
async function itemContainer(pid, cid, entries, size) {
  if (!CID.test(String(cid || ""))) return null;
  const old = await containerEntries(cid);
  const r = await physics.request({ op: "eco_icont", player: String(pid), cid, old,
                                    new: Array.isArray(entries) ? entries.slice(0, 64) : [],
                                    size: Math.max(1, Math.min(64, size | 0 || 24)) });
  if (r.erro) {
    console.warn(`[economia] ${pid} baú ${cid}: ${r.erro}`);
    return null;
  }
  await storeContainer(cid, r.entries, pid);
  return r;
}

// ---------------------------------------------------------------- eventos do Python
function onPhysicsEvent(ev) {
  if (ev.ev === "eco_save") { onSave(ev); return true; }
  if (ev.ev === "icont_store") {
    storeContainer(ev.cid, ev.entries, ev.player).catch((e) => console.warn("[economia]", e.message));
    return true;
  }
  if (ev.ev === "icont_delete") {
    deleteContainer(ev.cid).catch((e) => console.warn("[economia]", e.message));
    return true;
  }
  return false;
}

// Drops e almas de mobs (decididos pelo servidor dos mobs).
function credit(pid, items, souls) {
  physics.send({ op: "eco_credit", player: String(pid), items: items || [], souls: souls || 0 });
}

function killSouls(tipo) {
  const { mobTypes } = require("./mobs/mobServer");
  const cfg = mobTypes()[tipo] || {};
  const n = parseInt(cfg.almas, 10);
  return Number.isFinite(n) ? Math.max(0, n) : 25;      // SOULS_PER_KILL do jogo
}

module.exports = {
  configBundle, setOverrides, testMode, cleanStatus, load, ensure, reloadAll, flush,
  containerEntries, containerExists, itemContainer, onPhysicsEvent, credit, killSouls,
  CID,
};
