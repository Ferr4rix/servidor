// O que o servidor sabe do terreno de uma sala em uso: mundo base + alterações.
const fs = require("fs");
const path = require("path");
const config = require("../config");
const { generateTrees, baseBlock, key, plantCells } = require("./worldgen");

let BLOCK_DEFS = null;
function blockDefs() {
  if (!BLOCK_DEFS) {
    try {
      BLOCK_DEFS = JSON.parse(fs.readFileSync(path.join(config.dataDir, "blocks.json"), "utf8"));
    } catch (e) {
      console.warn("[mundo] blocks.json ilegível:", e.message);
      BLOCK_DEFS = {};
    }
  }
  return BLOCK_DEFS;
}

// Nomes antigos de blocos (= blocks/__init__.py LEGACY_BLOCK_IDS)
const LEGACY = { "mini sol": "berco solar" };

function canonicalType(value) {
  const t = value && typeof value === "object" ? (value.type || null) : (value || null);
  return t && LEGACY[t] ? LEGACY[t] : t;
}

class RoomWorld {
  constructor(roomId, data) {
    this.id = roomId;
    this.size_x = data.size_x | 0;
    this.size_y = data.size_y | 0;
    this.size_z = data.size_z | 0;
    this.seed = data.seed;
    this.geracao = data.geracao || null;      // "caixa": dungeon (ver worldgen.js)
    this.material = data.material || null;
    this.revestimento = data.revestimento || null;
    this.camadas = data.camadas || null;
    this.labirinto = data.labirinto || null;
    this.escadas = data.escadas || null;
    this.lagos = data.lagos || null;            // lagos escavados (worldgen.js)
    this.praca_raio = data.praca_raio || 0;
    this.meta = data;
    this.mods = new Map(Object.entries(data.modifications || {}));
    this.trees = generateTrees(data).trees;
    this.plantas = data.plantas || null;
    this.plants = plantCells(this, this.trees);   // plantas da criação (capim, flores, mini sóis)
    this._solid = {};
  }

  get(x, y, z) {
    const k = key(x, y, z);
    if (this.mods.has(k)) return canonicalType(this.mods.get(k));
    return baseBlock(this, this.trees, x, y, z);
  }

  set(k, value) {
    this.mods.set(String(k), value === undefined ? null : value);
  }

  solid(x, y, z) {
    if (y < 0) return true;
    if (x < 0 || z < 0 || x >= this.size_x || z >= this.size_z) return true;   // borda
    const t = this.get(x, y, z);
    if (!t) return false;
    if (!(t in this._solid)) {
      const def = blockDefs()[t];
      this._solid[t] = def ? def.solid !== false : true;
    }
    return this._solid[t];
  }
}

module.exports = { RoomWorld, blockDefs, canonicalType };
