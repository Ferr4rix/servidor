// Teste sem rede nem banco: mundo, mobs e caixas de colisão.
//   node tools/self_test.js
const { RoomWorld } = require("../src/world/roomWorld");
const { MobServer } = require("../src/mobs/mobServer");

const world = new RoomWorld("teste", { seed: 3, size_x: 30, size_y: 12, size_z: 30, modifications: {} });
let saved = null;
const srv = new MobServer("teste", world, async (id, f) => { saved = f; });
const gy = 8.0;
const player = { pos: [20, gy, 20], height: 3.6, half_width: 0.45, swing: 0 };
srv.updatePlayer("p1", player);
console.log("spawn:", srv.spawn("esqueleto viajante", [20, gy + 6, 34]).result);
let hits = 0;
const states = [];
for (let i = 0; i < 400; i++) {
  srv.updatePlayer("p1", player);
  srv.step(0.05);
  for (const ev of srv.drainEvents("p1")) if (ev.type === "player_hit") { hits++; if (hits <= 3) console.log("  acerto:", ev.part, ev.damage); }
  const m = srv.mobs.get("mob-1");
  if (m && (!states.length || states[states.length - 1][0] !== m.state)) states.push([m.state, (i * 0.05).toFixed(2), m.z.toFixed(2)]);
}
console.log("estados:", JSON.stringify(states.slice(0, 6)));
console.log("acertos no jogador:", hits);
const m = srv.mobs.get("mob-1");
console.log("golpe:", srv.attack("p1", "mob-1", 5, [20, gy + 3.2, 20]).result, m.health);
srv.setState(m, "block"); m.stateT = 0;
m.yaw = (Math.atan2(20 - m.x, 20 - m.z) * 180) / Math.PI;
console.log("bloqueio de frente:", srv.attack("p1", "mob-1", 5, [20, gy + 3.2, 20]).result);
m.yaw = (m.yaw + 180) % 360;
console.log("bloqueio de costas:", srv.attack("p1", "mob-1", 5, [20, gy + 3.2, 20]).result);
for (let i = 0; i < 300; i++) { srv.updatePlayer("p1", player); srv.step(0.05); }
console.log("mobs vivos:", srv.mobs.size);
srv.save().then(() => console.log("gravado:", saved.mobs.length, "mobs"));
