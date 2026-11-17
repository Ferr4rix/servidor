// Mundo base de uma sala, igual ao do cliente (CRUD_room.py):
//   y=0 pedra dura; y=1..2 terra; y=3 grama; árvores sorteadas com a semente da sala.
//   "praca_raio" na sala: sem árvores nesse raio à volta do centro (sala pública).
//   "geracao": "caixa" (dungeon): caixa oca de "material" (pedra dura) do
//   tamanho da sala — chão, teto e 4 paredes de 1 bloco; sem árvores. Dentro:
//     "revestimento": {bloco, fragmento, chance} — as paredes forradas por
//       dentro com "bloco" (pedra), com "fragmento" (fragmento solar) aqui e ali;
//     "camadas": {a_cada, inicio, tambem, bloco, fragmento, chance} — um piso
//       de "bloco" em "inicio" e depois a cada "a_cada" de altura (e nas
//       alturas de "tambem"), também com fragmentos espalhados;
//     "escadas": {por_andar: [min, max], bloco, luz} — escadas em caracol
//       largas (anel de 3 à volta de um pilar 3x3) que sobem de cada andar ao
//       de cima, com um buraco no piso de cima por cima dos degraus altos e
//       luz no topo do pilar (ver dungeonStairs);
//     "labirinto": {celula, aberto_extra, clareira, bloco, fragmento, chance}
//       — em cada andar (entre dois pisos) paredes do chão ao teto, num
//       labirinto de células "celula" x "celula" (ver mazeWall).
//   Onde vai cada fragmento sai de um hash da célula (cellHash), igual ao
//   CRUD_room.py, por isso o jogo e o servidor geram o mesmo.
const { PyRandom } = require("./pyrandom");

const BEDROCK_Y = 0;
const GROUND_LAYERS = 3;
const GROUND_Y = BEDROCK_Y + GROUND_LAYERS;
const TREE_BASE_Y = GROUND_Y + 1;
const TRUNK_HEIGHT = 5;

const key = (x, y, z) => `${x},${y},${z}`;

function treeBlocks(tx, tz, baseY = TREE_BASE_Y) {
  // Mesma ordem e regra "primeira escrita ganha" de blocks/structure_blocks/tree.py.
  const blocks = new Map();
  const put = (x, y, z, t) => { const k = key(x, y, z); if (!blocks.has(k)) blocks.set(k, [x, y, z, t]); };
  for (let i = 0; i < TRUNK_HEIGHT; i++) put(tx, baseY + i, tz, "wood");
  const y0 = baseY + 3;
  for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) put(tx + dx, y0, tz + dz, "leaves");
  for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) {
    if (Math.abs(dx) === 2 && Math.abs(dz) === 2) continue;
    put(tx + dx, y0 + 1, tz + dz, "leaves");
  }
  for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) put(tx + dx, y0 + 2, tz + dz, "leaves");
  for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
    if (Math.abs(dx) === 1 && Math.abs(dz) === 1) continue;
    put(tx + dx, y0 + 3, tz + dz, "leaves");
  }
  put(tx, y0 + 4, tz, "leaves");
  return [...blocks.values()];
}

// Devolve só o que não é chão (as árvores): o chão é calculado na hora.
function generateTrees(room) {
  if (room.geracao === "caixa" || room.geracao === "branca") return { trees: new Map(), trunks: [] };
  const { seed, size_x: sx, size_y: sy, size_z: sz } = room;
  const rng = new PyRandom(seed);
  const tries = Math.max(1, Math.floor((sx * sz) / 60));
  const placed = [];
  const trees = new Map();
  const lakes = lakesOf(room);
  for (let i = 0; i < tries; i++) {
    const bx = rng.randrange(2, sx - 2);
    const bz = rng.randrange(2, sz - 2);
    if (placed.some(([ax, az]) => (bx - ax) ** 2 + (bz - az) ** 2 < 25)) continue;
    const pr = room.praca_raio | 0;
    if (pr > 0 && (bx - Math.floor(sx / 2)) ** 2 + (bz - Math.floor(sz / 2)) ** 2 < pr * pr) continue;
    if (lakes.length && lakeDepth(lakes, bx, bz, 3)) continue;      // nem dentro nem à beira de um lago
    for (const [x, y, z, t] of treeBlocks(bx, bz)) {
      if (x < 0 || y < 0 || z < 0 || x >= sx || y >= sy || z >= sz) continue;
      if (y <= GROUND_Y) continue;                 // chão já existe ali
      const k = key(x, y, z);
      if (!trees.has(k)) trees.set(k, t);
    }
    placed.push([bx, bz]);
  }
  return { trees, trunks: placed };
}


// Lagos ("lagos": {quantidade, raio: [min, max]} na sala; só no chão normal):
// elipses escavadas na terra, com água. Tudo sai de cellHash (igual ao
// CRUD_room.py, em contas inteiras): o jogo e o servidor escavam o mesmo.
// Profundidade 2 no meio e 1 na borda. Sem lagos na praça nem colados uns aos
// outros; as árvores não nascem dentro nem a menos de 3 da borda.
const LAKE_SALT = 900;
function lakesOf(room) {
  const spec = room.lagos;
  if (!spec || room.geracao === "caixa" || room.geracao === "branca") return [];
  const seed = room.seed | 0, sx = room.size_x | 0, sz = room.size_z | 0;
  const want = Math.max(0, Number(spec.quantidade || 0) | 0);
  const r = Array.isArray(spec.raio) ? spec.raio : [3, 7];
  const rmin = Math.max(2, r[0] | 0), rmax = Math.max(rmin, r[1] | 0);
  const pr = room.praca_raio | 0;
  const out = [];
  for (let i = 0; i < want * 8 && out.length < want; i++) {
    const h = (k) => cellHash(seed + LAKE_SALT + k, i, 0, 0);
    const rx = rmin + Math.floor(h(1) * (rmax - rmin + 1));
    const rz = rmin + Math.floor(h(2) * (rmax - rmin + 1));
    const mx = 3 + rx, mz = 3 + rz;
    if (sx - 2 * mx <= 0 || sz - 2 * mz <= 0) continue;
    const cx = mx + Math.floor(h(3) * (sx - 2 * mx));
    const cz = mz + Math.floor(h(4) * (sz - 2 * mz));
    const big = Math.max(rx, rz);
    const dcx = cx - Math.floor(sx / 2), dcz = cz - Math.floor(sz / 2);
    if (pr > 0 && dcx * dcx + dcz * dcz < (pr + big + 4) * (pr + big + 4)) continue;
    if (out.some((l) => (l.cx - cx) ** 2 + (l.cz - cz) ** 2 < (Math.max(l.rx, l.rz) + big + 3) ** 2)) continue;
    out.push({ cx, cz, rx, rz });
  }
  return out;
}

// Profundidade do lago em (x, z) (0 = fora), com ``grow`` blocos a mais de raio.
function lakeDepth(lakes, x, z, grow = 0) {
  for (const l of lakes) {
    const rx = l.rx + grow, rz = l.rz + grow;
    const dx = x - l.cx, dz = z - l.cz;
    const num = dx * dx * rz * rz + dz * dz * rx * rx;
    const lim = rx * rx * rz * rz;
    if (num < lim) return grow ? 1 : (num * 100 < 45 * lim ? 2 : 1);
  }
  return 0;
}

// Mapa "x,z" -> profundidade de todas as células dos lagos.
function lakeCells(room) {
  const cells = new Map();
  for (const l of lakesOf(room)) {
    for (let x = l.cx - l.rx; x <= l.cx + l.rx; x++) {
      for (let z = l.cz - l.rz; z <= l.cz + l.rz; z++) {
        const d = lakeDepth([l], x, z);
        if (d) cells.set(`${x},${z}`, d);
      }
    }
  }
  return cells;
}

// A água dos lagos cheios: [["x,y,z", 8], ...] (a sala enche-se uma vez).
function lakeWater(room) {
  const out = [];
  for (const [k, d] of lakeCells(room)) {
    const [x, z] = k.split(",").map(Number);
    for (let y = GROUND_Y - d + 1; y <= GROUND_Y; y++) out.push([`${x},${y},${z}`, 8]);
  }
  return out;
}

// Número 0-1 fixo para (semente, x, y, z): mesmo cálculo do CRUD_room.py.
function cellHash(seed, x, y, z) {
  let h = (Math.imul(seed | 0, 0x9E3779B1) + Math.imul(x | 0, 0x85EBCA77)) >>> 0;
  h = (h + Math.imul(y | 0, 0xC2B2AE3D)) >>> 0;
  h = (h + Math.imul(z | 0, 0x27D4EB2F)) >>> 0;
  h ^= h >>> 15; h = Math.imul(h, 0x2C1B3C6D) >>> 0;
  h ^= h >>> 12; h = Math.imul(h, 0x297A2D39) >>> 0;
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

// Labirinto: grelha de células de "celula" blocos dentro do revestimento
// (x,z de 2 a size-3). Algoritmo "árvore binária" (cada célula abre para
// leste ou sul, sorteado pelo hash: dá sempre um labirinto com caminho para
// todo o lado) + "aberto_extra" de paredes abertas a mais (atalhos/voltas).
// Cada andar tem o seu labirinto. Clareira sem paredes à volta do centro no
// andar de baixo (o minoculus).
function mazeCarve(room, lab, n, i, j, level) {
  if (i === n - 1 && j === n - 1) return 0;
  if (i === n - 1) return 2;                        // 2 = sul
  if (j === n - 1) return 1;                        // 1 = leste
  return cellHash((room.seed | 0) + 10 + level, i, 0, j) < 0.5 ? 1 : 2;
}

function mazeWall(room, lab, x, y, z, level) {
  const c = Math.max(3, lab.celula | 0 || 14);
  const inner = room.size_x - 4;
  const n = Math.floor(inner / c);
  const rx = x - 2, rz = z - 2;
  if (rx <= 0 || rz <= 0 || rx >= n * c || rz >= n * c) return false;
  const onX = rx % c === 0, onZ = rz % c === 0;
  if (!onX && !onZ) return false;
  const cl = lab.clareira | 0;
  if (level === 0 && cl > 0 && Math.abs(x - Math.floor(room.size_x / 2)) <= cl
      && Math.abs(z - Math.floor(room.size_z / 2)) <= cl) return false;
  if (onX && onZ) return true;                      // pilar
  const extra = Number(lab.aberto_extra || 0);
  const salt = (room.seed | 0) + 20 + level;
  if (onX) {                                        // parede entre (i,j) e (i+1,j)
    const i = rx / c - 1, j = Math.floor(rz / c);
    return !(mazeCarve(room, lab, n, i, j, level) === 1 || cellHash(salt, i, 1, j) < extra);
  }
  const i = Math.floor(rx / c), j = rz / c - 1;     // parede entre (i,j) e (i,j+1)
  return !(mazeCarve(room, lab, n, i, j, level) === 2 || cellHash(salt, i, 2, j) < extra);
}

function layerBase(cam) { return cam && cam.inicio !== undefined ? Number(cam.inicio) : 0; }

function isLayer(cam, y) {
  if (!cam) return false;
  if (Array.isArray(cam.tambem) && cam.tambem.includes(y)) return true;
  const b = layerBase(cam);
  return cam.a_cada > 0 && y >= b && (y - b) % cam.a_cada === 0;
}

// Andar (0 = o de baixo) de uma célula de ar acima do piso de base.
function levelOf(cam, y) { return Math.floor((y - layerBase(cam)) / cam.a_cada); }

// y (células) de todos os pisos da caixa, de baixo para cima.
function floorsOf(room) {
  const cam = room.camadas || {};
  const ys = new Set((cam.tambem || []).map(Number));
  const step = Number(cam.a_cada || 0);
  if (step > 0) for (let y = layerBase(cam); y < Number(room.size_y) - 1; y += step) if (y > 0) ys.add(y);
  if (!ys.size) ys.add(0);
  return [...ys].sort((a, b) => a - b);
}

// ---------------------------------------------------------------- escadas
// Escada em caracol larga dentro de uma célula do labirinto: ocupa 9x9, com
// um pilar 3x3 no meio e um anel de 3 blocos de largura à volta, dividido em
// 10 setores (STAIR_SECTORS, fixo para dar igual no jogo e no servidor). O
// setor 0 é a entrada (no chão) e cada setor seguinte é 1 degrau mais alto
// (cheio por baixo); o último dá para o piso de cima, que tem um buraco por
// cima dos degraus altos (para a cabeça passar ao saltar). No topo do pilar,
// do lado dos degraus baixos, ficam fragmentos solares encostados ao teto:
// iluminam a escada sem ficarem onde se pisa nem ao alcance de quem sobe.
const STAIR_SECTORS = [
  "666777888",
  "666678888",
  "556678899",
  "555###999",
  "555###000",
  "444###000",
  "443321100",
  "333321111",
  "333222111",
];
const STAIR_SIZE = 9;
const _stairCache = new Map();

// Células locais da escada: [lx, lz, s] (s = setor 0..9; -1 = pilar).
function stairLocalCells() {
  const out = [];
  for (let lz = 0; lz < STAIR_SIZE; lz++) for (let lx = 0; lx < STAIR_SIZE; lx++) {
    const ch = STAIR_SECTORS[lz][lx];
    out.push([lx, lz, ch === "#" ? -1 : Number(ch)]);
  }
  return out;
}

function dungeonStairs(room) {
  const spec = room.escadas, cam = room.camadas, lab = room.labirinto;
  if (!spec || !cam || !lab || !(cam.a_cada > 0)) return null;
  const key = JSON.stringify([room.seed, room.size_x, room.size_z, room.size_y, cam, lab, spec]);
  const hit = _stairCache.get(key);
  if (hit) return hit;
  const seed = room.seed | 0;
  const c = Math.max(3, lab.celula | 0 || 14);
  const n = Math.floor((room.size_x - 4) / c);
  const S = STAIR_SIZE, off = 1 + Math.floor((c - 1 - S) / 2);
  const cl = (lab.clareira | 0) + 4;
  const cx = Math.floor(room.size_x / 2), cz = Math.floor(room.size_z / 2);
  const [lo, hi] = Array.isArray(spec.por_andar) ? spec.por_andar : [spec.por_andar || 3, spec.por_andar || 3];
  const floors = floorsOf(room);
  const cells = stairLocalCells();
  const sec = (lx, lz) => (lx < 0 || lz < 0 || lx >= S || lz >= S) ? null
    : (STAIR_SECTORS[lz][lx] === "#" ? -1 : Number(STAIR_SECTORS[lz][lx]));
  const map = new Map();          // "x,z" -> [{f, H, p}]  (p = altura; -1 pilar; -2 pilar com luz)
  const list = [];
  const add = (x, z, e) => { const k = x + "," + z; if (!map.has(k)) map.set(k, []); map.get(k).push(e); };
  let prev = new Set();
  for (let fi = 0; fi + 1 < floors.length; fi++) {
    const f = floors[fi], H = floors[fi + 1] - f;
    if (H > 10 || H < 4 || S + 2 > c - 1) { prev = new Set(); continue; }   // degraus de 1 bloco: só até 10
    const L = levelOf(cam, f + 1);
    const count = lo + Math.floor(cellHash(seed + 49, L, 7, 0) * (hi - lo + 1));
    const height = (s) => Math.floor(s * (H - 1) / 9);
    const used = new Set();
    for (let t = 0; t < 400 && used.size < count; t++) {
      const i = Math.floor(cellHash(seed + 50, L, t, 1) * n);
      const j = Math.floor(cellHash(seed + 51, L, t, 2) * n);
      const k = i + "," + j;
      if (used.has(k) || prev.has(k)) continue;              // nem a mesma célula do andar de baixo
      const bx = 2 + i * c, bz = 2 + j * c;
      if (fi === 0 && Math.abs(bx + c / 2 - cx) <= cl && Math.abs(bz + c / 2 - cz) <= cl) continue;   // clareira
      used.add(k);
      const rot = Math.floor(cellHash(seed + 52, L, t, 3) * 4);
      const mirror = cellHash(seed + 53, L, t, 4) < 0.5;
      const to = (lx, lz) => {          // local -> mundo (espelho e rotação de 90°)
        if (mirror) lz = S - 1 - lz;
        for (let r = 0; r < rot; r++) { const t2 = lx; lx = S - 1 - lz; lz = t2; }
        return [bx + off + lx, bz + off + lz];
      };
      list.push({ i, j, f, H, rot, mirror, x: to(0, 0)[0], z: to(0, 0)[1] });
      for (const [lx, lz, s] of cells) {
        let p;
        if (s >= 0) p = height(s);
        else {
          // pilar: luz no topo se só tiver degraus baixos à volta (fora de alcance)
          let ring = 0, low = true;
          for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) {
            const q = sec(lx + dx, lz + dz);
            if (q !== null && q >= 0) { ring++; if (height(q) > H - 6) low = false; }
          }
          p = ring > 0 && low ? -2 : -1;
        }
        const [x, z] = to(lx, lz);
        add(x, z, { f, H, p });
      }
    }
    prev = used;
  }
  const out = { map, list };
  _stairCache.set(key, out);
  return out;
}

// Bloco da escada em (x,y,z): o bloco, null (ar: por cima de um degrau ou
// buraco no piso) ou undefined (a escada não mexe nesta célula).
function stairAt(room, x, y, z) {
  const st = dungeonStairs(room);
  if (!st) return undefined;
  const here = st.map.get(x + "," + z);
  if (!here) return undefined;
  const bloco = room.escadas.bloco || "pedra";
  const luz = room.escadas.luz || null;
  for (const { f, H, p } of here) {
    if (y === f + H) {                                      // piso de cima
      if (p >= H - 4) return null;                          // buraco para a cabeça
      continue;
    }
    if (y > f && y < f + H) {
      const k = y - f;
      if (p === -2) return k === H - 1 && luz ? luz : bloco;   // pilar com luz no topo
      if (p === -1) return bloco;                              // pilar
      return k <= p ? bloco : null;                            // degrau (cheio por baixo) / ar
    }
  }
  return undefined;
}

function boxBlock(room, x, y, z) {
  const sx = room.size_x, sy = room.size_y, sz = room.size_z;
  if (x === 0 || y === 0 || z === 0 || x === sx - 1 || y === sy - 1 || z === sz - 1) {
    return room.material || "pedra dura";
  }
  const pick = (spec, salt) => {
    const frag = spec.fragmento;
    if (frag && cellHash((room.seed | 0) + salt, x, y, z) < Number(spec.chance || 0)) return frag;
    return spec.bloco || "pedra";
  };
  const rev = room.revestimento;
  if (rev && (x === 1 || z === 1 || x === sx - 2 || z === sz - 2)) return pick(rev, 1);
  const cam = room.camadas;
  if (room.escadas) {
    const st = stairAt(room, x, y, z);
    if (st !== undefined) return st;
  }
  if (isLayer(cam, y)) return pick(cam, 2);
  const lab = room.labirinto;
  if (lab && cam && cam.a_cada > 0 && y >= 2) {
    const level = levelOf(cam, y);
    if (mazeWall(room, lab, x, y, z, level)) return pick(lab, 3);
  }
  return null;
}

function baseBlock(room, trees, x, y, z) {
  if (x < 0 || z < 0 || y < 0 || x >= room.size_x || z >= room.size_z || y >= room.size_y) return null;
  if (room.geracao === "caixa") return boxBlock(room, x, y, z);
  // "branca" (Santuário do anjo): só um chão liso de "material", sem árvores
  if (room.geracao === "branca") return y <= GROUND_Y ? (room.material || "pedra branca") : null;
  if (y === BEDROCK_Y) return "pedra dura";
  if (y > BEDROCK_Y && y <= GROUND_Y && room.lagos) {
    if (!room._lakeCells) Object.defineProperty(room, "_lakeCells", { value: lakeCells(room), enumerable: false });
    const d = room._lakeCells.get(`${x},${z}`);
    if (d && y > GROUND_Y - d) return null;        // escavado: é água (rooms.js enche)
  }
  if (y < GROUND_Y) return "terra";
  if (y === GROUND_Y) return "grama";
  const t = trees.get(key(x, y, z));
  if (t) return t;
  if (y === GROUND_Y + 1 && room.plantas) return plantAt(room, x, z);
  return null;
}

// Plantas já na criação ("plantas" na sala: {"capim": 0.008, ...} = chance
// por bloco de grama). Em cima da grama (não nos lagos, na praça nem na
// borda); a árvore ganha. Mesmo cálculo do CRUD_room.py (_generate_plants).
const PLANT_SALT = 700;
function plantAt(room, x, z) {
  const sx = room.size_x, sz = room.size_z;
  if (x < 1 || z < 1 || x > sx - 2 || z > sz - 2) return null;
  const pr = room.praca_raio || 0;
  const dx = x - Math.floor(sx / 2), dz = z - Math.floor(sz / 2);
  if (pr > 0 && dx * dx + dz * dz <= (pr + 2) * (pr + 2)) return null;
  if (room.lagos) {
    if (!room._lakeCells) Object.defineProperty(room, "_lakeCells", { value: lakeCells(room), enumerable: false });
    if (room._lakeCells.has(`${x},${z}`)) return null;
  }
  const h = cellHash((room.seed | 0) + PLANT_SALT, x, GROUND_Y + 1, z);
  let acc = 0;
  for (const [tipo, p] of Object.entries(room.plantas)) {
    acc += Number(p) || 0;
    if (h < acc) return tipo;
  }
  return null;
}

// Todas as plantas da criação: Map "x,y,z" -> tipo (as fadas nascem dos mini sóis).
function plantCells(room, trees) {
  const out = new Map();
  if (!room.plantas || room.geracao) return out;
  for (let x = 1; x < room.size_x - 1; x++) {
    for (let z = 1; z < room.size_z - 1; z++) {
      const k = key(x, GROUND_Y + 1, z);
      if (trees.has(k)) continue;
      const t = plantAt(room, x, z);
      if (t) out.set(k, t);
    }
  }
  return out;
}

module.exports = { generateTrees, baseBlock, treeBlocks, cellHash, floorsOf, dungeonStairs, GROUND_Y, key,
                   lakesOf, lakeCells, lakeWater, plantAt, plantCells };
