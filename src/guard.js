// Guarda: o servidor confere o que o jogo diz sobre dano, velocidade e posição.
//
// O perfil de cada jogador ("o que ele consegue") vem do Python
// (server/physics/economy.py, profile): força e velocidade dos pontos de
// almas, as armas que ele TEM e o peso que leva. Com isso:
//
//   dano     cada golpe é limitado ao máximo que a arma na mão (ou o soco)
//            pode dar com a força dele, x o maior multiplicador do combate
//            (finalizador x golpe carregado/contra-ataque). Arma que ele não
//            tem = soco. E cada alvo só leva golpes ao ritmo das armas dele.
//   posição  o jogador só anda o que a velocidade dele deixa (com folga para
//            empurrões e saltos). Passou disso: o servidor não aceita a posição
//            e manda o jogo voltar ao último lugar bom ({t:"pos_fix"}).
//   entrar   só se aparece perto de um ponto de entrada da sala (última
//            posição gravada, ponto de spawn, centro); morrer volta ao spawn.
const SIZE = 2.0;                  // unidades por bloco
const JOIN_RADIUS = 8 * SIZE;      // longe disto do ponto de entrada = corrigido
const SLACK_H = 12 * SIZE;         // folga horizontal acumulável (empurrões, desencalhar)
const SLACK_UP = 14 * SIZE;        // folga para subir (saltos, escadas, desencalhar)
const UP_SPEED = 26.0;             // unidades/s a subir sem voo (salto)
const FLY_UP = 12.0 * 1.3;         // a voar (player.FLY_SPEED_UP) + folga
const MAX_DT = 1.0;                // um pacote atrasado não dá mais do que isto
const FIX_COOLDOWN_MS = 400;       // não mandar correções a cada pacote
const MAX_RISE = 10 * SIZE;        // sem botas de voo: subir sem parar no máximo isto
const REST_TO_RESET = 0.3;         // s sem subir para "assentar" (chão / degrau)
const HIT_BURST = 2;               // golpes seguidos tolerados no mesmo alvo (rede)

const DEFAULT_PROFILE = {
  soco: { dano: 8, intervalo: 0.2 }, armas: {}, voo: false, vmax: 92, impacto: 50,
};

const profiles = new Map();        // pid -> perfil

function setProfile(pid, p) { profiles.set(String(pid), p || DEFAULT_PROFILE); }
function profileOf(pid) { return profiles.get(String(pid)) || DEFAULT_PROFILE; }

// ---------------------------------------------------------------- dano
// Limite de um golpe: a melhor das mãos (arma na mão, 2ª mão / escudo) que
// ele tem de verdade; nada disso = soco.
function hitLimit(pid, state, kind) {
  const p = profileOf(pid);
  if (kind === "impacto" || kind === "projetil") return { dano: p.impacto, intervalo: 0.05 };
  let best = p.soco;
  for (const item of [state && state.held, state && state.offhand]) {
    const w = item && p.armas[String(item)];
    if (w && w.dano > best.dano) best = w;
  }
  return best;
}

// Ritmo por alvo (balde de golpes): o mesmo mob/jogador não leva golpes mais
// depressa do que a arma deixa (com HIT_BURST de folga para a rede).
function hitAllowed(info, target, intervalo) {
  const now = Date.now() / 1000;
  info.hitBuckets = info.hitBuckets || new Map();
  const b = info.hitBuckets.get(target) || { tokens: HIT_BURST, t: now };
  b.tokens = Math.min(HIT_BURST, b.tokens + (now - b.t) / Math.max(0.02, intervalo));
  b.t = now;
  if (info.hitBuckets.size > 200) info.hitBuckets.clear();
  if (b.tokens < 1) { info.hitBuckets.set(target, b); return false; }
  b.tokens -= 1;
  info.hitBuckets.set(target, b);
  return true;
}

// Confere um golpe: devolve o dano aceite (0 = recusado) e porquê.
function checkHit(info, target, damage, kind) {
  const lim = hitLimit(info.playerId, info.state, kind);
  damage = Math.max(0, Number(damage) || 0);
  if (!hitAllowed(info, `${kind === "impacto" ? "i" : "g"}:${target}`, lim.intervalo)) {
    return { damage: 0, motivo: "rápido demais" };
  }
  if (damage > lim.dano) {
    info.cutHits = (info.cutHits || 0) + 1;
    if (info.cutHits % 20 === 1) {
      console.warn(`[guarda] ${info.nome}: dano ${damage.toFixed(1)} > máximo ${lim.dano} (${kind})`);
    }
    return { damage: lim.dano, motivo: "dano acima do possível" };
  }
  return { damage };
}

// ---------------------------------------------------------------- movimento
function near2d(a, b, r) {
  return Math.hypot(a[0] - b[0], a[2] - b[2]) <= r;
}

function parseVec(v) {
  if (typeof v === "string") v = v.split(",");
  if (!Array.isArray(v) || v.length !== 3) return null;
  const out = v.map(Number);
  return out.every(Number.isFinite) ? out : null;
}

// Pontos onde se pode aparecer nesta sala (entrar / renascer).
function entryPoints(player, room) {
  const out = [];
  const lp = parseVec(((player && player.last_position) || {})[room.id]);
  const sp = parseVec(((player && player.spawn_point) || {})[room.id]);
  if (lp) out.push(lp);
  if (sp) out.push(sp);
  const sx = (room.world && room.world.size_x) || 40;
  const sz = (room.world && room.world.size_z) || 40;
  out.push([(Math.floor(sx / 2) + 3) * SIZE, 6, Math.floor(sz / 2) * SIZE]);
  return out;
}

// Ao entrar na sala (hello): guarda os pontos de entrada.
function onJoin(info, player, room) {
  info.move = { pos: null, t: 0, budgetH: SLACK_H, budgetUp: SLACK_UP, spawn: true,
                entries: entryPoints(player, room), lastFix: 0, fixes: 0, rise: 0, rest: 0 };
}

// Um empurrão que o servidor deu (golpe de mob): mais folga.
function grantSlack(info, blocks) {
  if (info && info.move) {
    info.move.budgetH = Math.min(SLACK_H * 2, info.move.budgetH + blocks * SIZE);
    info.move.budgetUp = Math.min(SLACK_UP * 2, info.move.budgetUp + blocks * SIZE);
  }
}

// Subir: sem botas de voo, subir sem parar (mais do que MAX_RISE além do
// que andou na horizontal, como numa escada) é voar.
// Confere a posição de um {t:"state"}. Devolve null (aceite) ou a posição
// para onde o jogo tem de voltar.
function checkMove(info, pos, dead) {
  const m = info.move;
  if (!m) return null;
  const now = Date.now() / 1000;
  const prof = profileOf(info.playerId);
  const wasDead = !!(info.state && info.state.dead);
  if (m.pos === null || m.spawn || (wasDead && !dead)) {
    // a aparecer (entrou, renasceu): tem de ser perto de um ponto de entrada
    const ok = m.entries.some((e) => near2d(pos, e, JOIN_RADIUS));
    if (!ok && m.pos !== null) return fix(info, m.pos);
    if (!ok) {
      m.pos = m.entries[m.entries.length - 1];
      m.t = now;
      m.spawn = false;
      return fix(info, m.pos);
    }
    m.pos = pos.slice();
    m.t = now;
    m.spawn = false;
    m.budgetH = SLACK_H;
    m.budgetUp = SLACK_UP;
    return null;
  }
  const dt = Math.min(MAX_DT, Math.max(0, now - m.t));
  const vmax = Number(prof.vmax) || DEFAULT_PROFILE.vmax;
  m.budgetH = Math.min(SLACK_H, m.budgetH + vmax * dt);
  const upRate = (prof.voo ? Math.max(UP_SPEED, FLY_UP) : UP_SPEED);
  m.budgetUp = Math.min(SLACK_UP, m.budgetUp + upRate * dt);
  const dh = Math.hypot(pos[0] - m.pos[0], pos[2] - m.pos[2]);
  const up = Math.max(0, pos[1] - m.pos[1]);
  // sem voo: subir sem nunca assentar é voar (salto/degraus param de subir)
  if (pos[1] - m.pos[1] <= 0.01) {
    m.rest += dt;
    if (m.rest >= REST_TO_RESET) m.rise = 0;
  } else {
    m.rest = 0;
  }
  const flying = !prof.voo && m.rise + up > MAX_RISE;
  if (dh > m.budgetH || up > m.budgetUp || flying) {
    m.budgetH = Math.max(0, m.budgetH);        // não fica a dever
    m.t = now;
    return fix(info, m.pos);
  }
  m.budgetH -= dh;
  m.budgetUp -= up;
  // degraus/encostas: sobe-se no máximo 1 bloco por bloco andado
  m.rise = Math.max(0, m.rise + up - dh);
  m.pos = pos.slice();
  m.t = now;
  return null;
}

function fix(info, pos) {
  const m = info.move;
  const now = Date.now();
  m.fixes += 1;
  if (m.fixes % 10 === 1) {
    console.warn(`[guarda] ${info.nome}: movimento impossível (${m.fixes}x), volta a ${pos.map((v) => v.toFixed(1))}`);
  }
  if (now - m.lastFix < FIX_COOLDOWN_MS) return { pos, silent: true };
  m.lastFix = now;
  return { pos };
}

// Posição boa (para gravar e para conferir distâncias).
function goodPos(info) {
  return info && info.move && info.move.pos ? info.move.pos : null;
}

module.exports = { setProfile, profileOf, checkHit, hitLimit, onJoin, checkMove, grantSlack,
                   goodPos, entryPoints, SIZE };
