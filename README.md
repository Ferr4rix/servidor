# Servidor do jogo — v0.6

Esta pasta é **só sua**: tem o servidor, as configurações do jogo e as
ferramentas de desenvolvimento. Aos jogadores entrega-se apenas a pasta
`../cliente` (ou o zip dela).

```
servidor/
├── docker-compose.yml, Dockerfile   sobe tudo com um comando
├── .env.example                     modelo do .env (string do MongoDB, portas...)
├── src/, physics/, tools/           servidor Node (HTTP + ENet) e física em Python
├── config/                          configurações do jogo: itens, blocos, mobs,
│                                    receitas, combate, balanceamento, economia
├── jogo/                            cópia do código do jogo que a física usa
│                                    (vem do cliente: sincronizar_do_cliente.py)
├── sincronizar_do_cliente.py
└── desenvolvimento/                 NÃO vai para o servidor nem para os jogadores
    ├── tools/                       gerar modelos, compilar os .exe, empacotar o cliente
    ├── modelos_fonte/               os .bbmodel (Blockbench) dos modelos do jogo
    ├── saves_modo_local/            rooms.json / players.json antigos (modo local)
    └── README_tecnico.md            documentação completa de todos os sistemas
```

## 1. Ligar o servidor

1. Instale o [Docker](https://www.docker.com/products/docker-desktop/) na máquina que vai ser o servidor.
2. Nesta pasta, copie `.env.example` para **`.env`** e preencha `MONGODB_URI` com a sua string do MongoDB Atlas.
   O `.env` nunca vai para o zip, o git nem a imagem Docker — não ponha a string em mais lado nenhum.
   > Na 0.5 o `.env` ficava na raiz do projeto; agora fica **aqui, em `servidor/`**. Pode copiar o antigo.
3. No Atlas → *Network Access*, libere o IP público da máquina do servidor.
4. `docker compose up --build -d` (o `-d` deixa-o a correr em segundo plano; `docker compose logs -f` mostra o registo).

Deve aparecer `[db] ligado ao MongoDB`, `[http] Express em ...:3000` e `[enet] tempo real em udp://...:7777`.
Teste no navegador: `http://ENDEREÇO:3000/api/health`.

**Modo de testes**: no `.env.example` da 0.6 vem `MODO_TESTES=0` (sem F6/F7/F8 a dar almas e água). Para testar sozinho ponha `1`.

## 2. Deixar os jogadores chegarem ao servidor

O servidor usa duas portas: **3000/TCP** (login, mundos) e **7777/UDP** (tempo real). As duas têm de estar abertas de fora.

- **VPS / servidor na nuvem** (recomendado: fica sempre ligado e tem IP fixo — qualquer Linux com 2 GB de RAM serve): instale o Docker, copie esta pasta (sem `desenvolvimento/`, que não é precisa), crie o `.env` e faça o passo 1. Abra as portas 3000/tcp e 7777/udp na firewall do fornecedor.
- **No seu computador**: no roteador, redirecione 3000/TCP e 7777/UDP para o IP do seu PC, e libere-as na Firewall do Windows. O seu IP de casa pode mudar; um domínio grátis com DNS dinâmico (ex.: DuckDNS) resolve.

**Senhas**: o login vai por HTTP simples. Para o pôr em HTTPS, ponha à frente do servidor um proxy com certificado (ex.: Caddy, `seu-dominio.com { reverse_proxy localhost:3000 }`) e use `"http": "https://seu-dominio.com"` no cliente. O tempo real (UDP) continua na porta 7777.

## 3. Preparar o jogo para distribuir

1. Em `../cliente/servidor.json` ponha o endereço **público** do servidor:
   ```json
   {"modo": "online", "http": "http://SEU_IP_OU_DOMINIO:3000", "enet_host": "SEU_IP_OU_DOMINIO", "enet_port": 7777}
   ```
2. (Só se mudou o launcher) `python desenvolvimento/tools/compilar_launchers.py` — precisa do Go; põe `Instalar.exe` e `Jogar.exe` no cliente.
3. `python desenvolvimento/tools/empacotar_cliente.py` → `desenvolvimento/dist/jogo_v0.6.zip`. Recusa se o `servidor.json` ainda apontar para `localhost`.
4. Mande o zip. O jogador descompacta, abre o `Instalar.exe` uma vez e depois o `Jogar.exe` (está no `LEIA-ME.txt` do cliente).

O cliente não leva configurações (itens, dano, receitas...): recebe-as do servidor ao entrar. Mudar `config/` e reiniciar o servidor muda o jogo de todos **sem** distribuir nada.

## 4. Atualizar

| Mudou… | Faça |
|---|---|
| algo em `config/` (itens, blocos, mobs, receitas, combate, economia) | `docker compose up --build -d` |
| código do servidor (`src/`, `physics/`) | `docker compose up --build -d` |
| código do jogo que o servidor também usa (física, água, fogo, capim, blocos, itens) | `python sincronizar_do_cliente.py` e depois `docker compose up --build -d` |
| só o cliente (gráficos, interface, modelos…) | empacotar e distribuir o cliente de novo |
| um modelo de mob | os dois: sincronizar + rebuild, e distribuir o cliente |

`python sincronizar_do_cliente.py --ver` só mostra o que está diferente, sem copiar.

**Jogadores com versões diferentes**: blocos/itens novos precisam do cliente novo (modelos e código). Ao mudar de versão, mande o zip novo a todos.

## 5. Moderadores e saves antigos

- Dar papel de moderador/admin: `docker compose run --rm servidor node tools/moderador.js <usuario> [admin|remover]` (`--lista` mostra quem tem).
- Importar os saves do modo local (uma vez): `docker compose run --rm -v "$(pwd)/desenvolvimento/saves_modo_local:/dados" servidor node tools/import_json.js /dados` (no cmd do Windows: `"%cd%\desenvolvimento\saves_modo_local:/dados"`).

## Ferramentas de desenvolvimento (`desenvolvimento/tools/`)

Escrevem diretamente na pasta do cliente (`caminhos.py` diz onde é cada coisa):
`gerar_capim.py`, `gerar_flor_lunar.py`, `gerar_mini_sol.py` (berço solar), `gerar_fada.py`, `mob_anim_comer_beber.py`, `torque_modelos.py`, `bbmodel_para_gltf.py`, `arma_para_jogo.py`… Depois de mudar um modelo de mob, rode o `sincronizar_do_cliente.py`.
