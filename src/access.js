// Acesso às salas públicas de nível 2+ (dungeons). Elas não entram na lista
// de mundos do jogador (link_room_properties): só se entra a partir de uma
// sala pública do nível anterior (a dungeon 1 = nível 2, pela praça = nível 1).
// Quem entra assim recebe um passe em memória para aquela sala: vale enquanto
// está lá e mais um pouco depois de sair (recarregar / cair a ligação).
const PASS_MS = 2 * 60 * 1000;
const passes = new Map();          // playerId -> { room, until }

function grant(pid, roomId, ms = PASS_MS) {
  passes.set(String(pid), { room: String(roomId), until: Date.now() + ms });
}

function has(pid, roomId) {
  const p = passes.get(String(pid));
  if (!p || p.room !== String(roomId)) return false;
  if (p.until < Date.now()) { passes.delete(String(pid)); return false; }
  return true;
}

// Nível de uma sala (as públicas sem "nivel" gravado são nível 1).
function levelOf(room) {
  if (!room || !room.publica) return 0;
  return Math.max(1, Number(room.nivel || 1) | 0);
}

// Pode entrar numa sala de nível ``target`` estando agora em ``current``?
function canEnterLevel(target, current, currentId, targetId) {
  if (target <= 1) return true;
  if (currentId && String(currentId) === String(targetId)) return true;
  return current === target - 1;
}

module.exports = { grant, has, levelOf, canEnterLevel, PASS_MS };
