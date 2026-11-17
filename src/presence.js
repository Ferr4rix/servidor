// Quem está online: uma conta só pode estar aberta num lugar de cada vez.
//
// Está online quem:
//   * está ligado ao tempo real (ENet) numa sala, ou
//   * falou com o servidor (qualquer pedido HTTP com sessão, incluindo o
//     "sinal de vida" que o jogo manda a cada 10 s, também no menu dos
//     mundos) há menos de ONLINE_SECONDS.
//
// O login de uma conta online é recusado. Ao sair do jogo, o cliente chama
// /api/logout e a conta fica livre na hora; se o jogo fechar sem avisar
// (crash, falta de luz), fica livre ao fim de ONLINE_SECONDS.
//
// Fica só em memória: o servidor é um processo só. Depois de reiniciar, os
// jogos que continuam abertos voltam a aparecer no próximo sinal de vida.

const ONLINE_SECONDS = parseFloat(process.env.PRESENCE_SECONDS || "30");

const seen = new Map();   // player_id -> { token, at }

function connectedRealtime(pid) {
  try {
    const rt = require("./realtime/server");
    for (const info of rt.peers.values()) if (String(info.playerId) === String(pid)) return true;
  } catch (e) {
    // tempo real ainda não arrancou
  }
  return false;
}

function isOnline(pid, now = Date.now()) {
  if (connectedRealtime(pid)) return true;
  const s = seen.get(String(pid));
  return !!s && now - s.at < ONLINE_SECONDS * 1000;
}

function touch(pid, token = null, now = Date.now()) {
  const key = String(pid);
  const s = seen.get(key);
  seen.set(key, { token: token || (s && s.token) || null, at: now });
}

// Saiu: a conta fica livre já (só se for a sessão que estava registada).
function release(pid, token = null) {
  const key = String(pid);
  const s = seen.get(key);
  if (!s) return;
  if (token && s.token && s.token !== token) return;
  seen.delete(key);
}

// Limpa entradas velhas de vez em quando (não cresce sem fim).
setInterval(() => {
  const now = Date.now();
  for (const [k, s] of seen) if (now - s.at > ONLINE_SECONDS * 4000) seen.delete(k);
}, 60 * 1000).unref();

module.exports = { isOnline, touch, release, ONLINE_SECONDS };
