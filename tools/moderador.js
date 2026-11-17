// Dá (ou tira) o papel de moderador a uma conta. Só o dono do servidor
// consegue (precisa do .env com o MongoDB). Moderadores entram no Painel de
// administração (projeto admin/) com o usuário e a senha do jogo.
//
//   docker compose run --rm servidor node tools/moderador.js <usuario>            dá "moderador"
//   docker compose run --rm servidor node tools/moderador.js <usuario> admin      dá "admin"
//   docker compose run --rm servidor node tools/moderador.js <usuario> remover    tira o papel
//   docker compose run --rm servidor node tools/moderador.js --lista              quem tem papel
// (sem Docker, na pasta server/:  node tools/moderador.js ...)
const db = require("../src/db");

async function main() {
  const [usuario, acao] = process.argv.slice(2);
  if (!usuario) {
    console.log("uso: node tools/moderador.js <usuario> [moderador|admin|remover] | --lista");
    process.exit(1);
  }
  await db.connect();
  const players = db.db.collection("players");
  if (usuario === "--lista") {
    const list = await players.find({ papel: { $in: ["moderador", "admin"] } },
      { projection: { usuario: 1, nome: 1, papel: 1 } }).toArray();
    if (!list.length) console.log("ninguém tem papel de moderador");
    for (const p of list) console.log(`${p.usuario} (${p.nome || "-"}): ${p.papel}`);
  } else {
    const p = await db.findPlayerByUser(usuario);
    if (!p) {
      console.log(`a conta "${usuario}" não existe`);
    } else if (acao === "remover") {
      await players.updateOne({ _id: p._id }, { $unset: { papel: "" } });
      await db.db.collection("admin_sessions").deleteMany({ player: p._id });
      console.log(`${usuario}: já não é moderador`);
    } else {
      const papel = acao === "admin" ? "admin" : "moderador";
      await players.updateOne({ _id: p._id }, { $set: { papel } });
      console.log(`${usuario}: agora é ${papel}`);
    }
  }
  await db.close();
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
