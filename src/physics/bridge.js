// Ponte para a física dos blocos em Python (server/physics/worker.py).
//
// O processo Python corre a MESMA física do jogo, sem gráficos. Aqui só se
// arranca o processo, se mandam os pedidos (linhas JSON) e se entregam os
// resultados a quem os quer (realtime/server.js). Se o processo morrer, é
// arrancado de novo e as salas abertas são reabertas com o estado gravado.
const path = require("path");
const readline = require("readline");
const { spawn } = require("child_process");
const config = require("../config");

const PYTHON = process.env.PHYSICS_PYTHON || "python3";
const SCRIPT = process.env.PHYSICS_SCRIPT || path.resolve(__dirname, "../../physics/worker.py");

let proc = null;
let ready = false;
let queue = [];               // pedidos à espera do processo
let onEvent = () => {};
let reopen = async () => [];  // () -> [{room, data}] das salas abertas
let restarts = 0;
let generation = 0;           // muda a cada processo novo (livros a recarregar)
let nextReq = 1;
const pending = new Map();    // req -> {resolve, timer}

function start(handler, reopenRooms) {
  onEvent = handler;
  reopen = reopenRooms;
  spawnWorker();
}

function spawnWorker() {
  ready = false;
  generation += 1;
  proc = spawn(PYTHON, ["-u", SCRIPT], {
    env: { ...process.env, GAME_DIR: config.dataDir, PYTHONIOENCODING: "utf-8" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const rl = readline.createInterface({ input: proc.stdout });
  rl.on("line", (line) => {
    let msg;
    try { msg = JSON.parse(line); } catch (e) { return; }
    if (msg.ev === "reply") {
      const p = pending.get(msg.req);
      if (p) {
        pending.delete(msg.req);
        clearTimeout(p.timer);
        p.resolve(msg);
      }
      return;
    }
    if (msg.ev === "ready") {
      ready = true;
      console.log("[física] processo Python pronto");
      const pending = queue;
      queue = [];
      for (const m of pending) write(m);
      return;
    }
    try { onEvent(msg); } catch (e) { console.warn("[física] evento:", e.message); }
  });
  readline.createInterface({ input: proc.stderr }).on("line", (l) => console.log(l));
  proc.on("exit", (code) => {
    console.warn(`[física] processo Python saiu (código ${code}); a reiniciar...`);
    proc = null;
    ready = false;
    const delay = Math.min(10000, 500 * 2 ** Math.min(restarts++, 5));
    setTimeout(async () => {
      spawnWorker();
      try {
        for (const r of await reopen()) send({ op: "open", room: r.room, data: r.data });
      } catch (e) {
        console.warn("[física] reabrir salas:", e.message);
      }
    }, delay);
  });
  proc.on("error", (e) => console.error(`[física] não foi possível arrancar ${PYTHON}:`, e.message));
}

function write(msg) {
  try {
    proc.stdin.write(JSON.stringify(msg) + "\n");
  } catch (e) {
    queue.push(msg);
  }
}

function send(msg) {
  if (!proc || !ready) {
    queue.push(msg);
    return;
  }
  write(msg);
}

// Pedido com resposta: devolve uma Promise com o {"ev":"reply"} do Python
// (rejeita se ele não responder a tempo).
function request(msg, timeoutMs = 8000) {
  const req = nextReq++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(req);
      reject(new Error(`física: sem resposta a ${msg.op}`));
    }, timeoutMs);
    pending.set(req, { resolve, timer });
    send({ ...msg, req });
  });
}

function stop() {
  if (proc) {
    try { proc.stdin.end(); } catch (e) { /* ok */ }
    proc.kill();
  }
}

module.exports = { start, send, request, stop, generation: () => generation };
