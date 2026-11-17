// Adaptador fino sobre o pacote "enet" (ENet compilado para JavaScript).
// Tudo o que depende da API do pacote está aqui; o resto do servidor só vê:
//   startHost(port, { onConnect(peer), onMessage(peer, obj), onDisconnect(peer) })
//   send(peer, obj)
const enet = require("enet");

function makePacket(buf, reliable = true) {
  const flags = !reliable ? 0                   // 0 = não fiável (sequenciado)
    : (enet.PACKET_FLAG && enet.PACKET_FLAG.RELIABLE !== undefined
      ? enet.PACKET_FLAG.RELIABLE : 1);        // 1 = ENET_PACKET_FLAG_RELIABLE
  return new enet.Packet(buf, flags);
}

// reliable=false: o pacote pode perder-se (o próximo substitui-o) e não
// atrasa os outros; vai no canal 1 para não se misturar com os fiáveis.
function send(peer, obj, channel = 0, reliable = true) {
  try {
    peer.send(reliable ? channel : 1, makePacket(Buffer.from(JSON.stringify(obj), "utf8"), reliable));
  } catch (e) {
    // par desligado no meio: ignora
  }
}

function decode(packet) {
  const data = typeof packet.data === "function" ? packet.data() : packet;
  return JSON.parse(Buffer.from(data).toString("utf8"));
}

function startHost(port, handlers) {
  return new Promise((resolve, reject) => {
    enet.createServer({
      address: new enet.Address("0.0.0.0", port),
      peers: 64,
      channels: 2,
      down: 0,
      up: 0,
    }, (err, host) => {
      if (err) return reject(err);
      host.on("connect", (peer) => {
        handlers.onConnect(peer);
        peer.on("message", (packet) => {
          let msg;
          try {
            msg = decode(packet);
          } catch (e) {
            return;                               // mensagem malformada
          }
          try {
            handlers.onMessage(peer, msg);
          } catch (e) {
            console.error("[enet] erro ao tratar mensagem:", e);
          }
        });
        peer.once("disconnect", () => handlers.onDisconnect(peer));
      });
      host.start(10);            // lê eventos a cada 10 ms
      resolve(host);
    });
  });
}

module.exports = { startHost, send };
