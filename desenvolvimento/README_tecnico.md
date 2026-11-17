# Jogo V0.6 — documentação técnica

> **v0.6: o projeto foi dividido em duas pastas** (ver `../../LEIA-ME.md` e `../README.md`).
> Os caminhos abaixo foram escritos para a 0.5 (tudo numa pasta); hoje:
> - código e assets do jogo → `cliente/` (o que vai para os jogadores);
> - `server/src`, `server/physics`, `server/tools` → `servidor/src`, `servidor/physics`, `servidor/tools`;
> - `blocks.json`, `mobs.json`, `itens.json`, `item_balancing.json`, `receitas.json`, `combate.json`, `economia.json` → `servidor/config/`;
> - `tools/` → `servidor/desenvolvimento/tools/`; os `.bbmodel` → `servidor/desenvolvimento/modelos_fonte/`;
> - o `.env` → `servidor/.env`; o Dockerfile e o docker-compose.yml → `servidor/`;
> - o código do jogo que a física do servidor usa é copiado do cliente para `servidor/jogo/` por `servidor/sincronizar_do_cliente.py`.
> - O **modo local** (`"modo": "local"`, sem servidor) precisa dos `.json` de `servidor/config/` copiados para a pasta do cliente; o cliente distribuído só joga online.


## Versão 0.5: servidor online (Express + ENet + MongoDB Atlas)

A partir da 0.5 o jogo fala com um **servidor** (pasta `server/`, Node.js):

| parte | tecnologia | o quê |
|---|---|---|
| contas, mundos, saves | **Express** (HTTP, porta 3000/TCP) | login, criar conta, lista/criação de mundos, ler a sala, gravar jogador, baús, cola, ligações |
| tempo real | **ENet** (UDP, porta 7777) | posição e animação dos jogadores (20x/s), blocos colocados/tirados, golpes em mobs, estado dos mobs |
| banco de dados | **MongoDB Atlas** | `players`, `rooms`, `chunks` (o mundo, por 16 x 16 colunas), `journal` (diário das mudanças), `containers`, `sessions` |

**Multiplayer**: vários jogadores na mesma sala veem-se uns aos outros (modelo, nome por cima — só visível agachado `Shift` ou com o Inspecionar `F4` —, andar, correr, agachar, sentar, item na mão, golpes) e veem os blocos que os outros colocam/tiram. **Os mobs vivem no servidor** (`server/src/mobs/`, porte em JavaScript do `backend/mobs.py`): todos veem os mesmos mobs, e o servidor decide posição, IA, golpes, escudo e dano (o cliente só pede). O terreno base é gerado com a semente da sala pelo cliente e pelo servidor de forma **idêntica** (o servidor reimplementa o gerador aleatório do Python, `server/src/world/pyrandom.js`), por isso os mobs andam no mesmo mundo que se vê.

A física dos blocos (queda, tombar, empurrar, quebrar) corre no **servidor** e todos veem a mesma coisa (ver "A física dos blocos corre no servidor"); ainda falta sincronizar a armadura dos outros jogadores não aparece.

### Uma conta, um lugar

Não dá para entrar na mesma conta duas vezes ao mesmo tempo. Antes, dois jogos na mesma conta gravavam um por cima do outro e tudo se misturava.
- **O login é recusado se a conta já está online**, com a mensagem *"Esta conta já está online. Saia do jogo no outro computador (se ele fechou sem sair, espere 30 s) e tente de novo."*.
- **O que conta como online** (`server/src/presence.js`):
  - estar ligado a uma sala (tempo real), ou
  - ter falado com o servidor nos últimos **30 s** (`PRESENCE_SECONDS` no `.env`). Qualquer pedido com sessão conta, e o jogo manda um **sinal de vida** (`POST /api/presence`) a cada 10 s, também no menu dos mundos.
- **Ao sair do jogo** (menu do Esc, fechar a janela ou voltar ao login), o jogo grava tudo e chama `POST /api/logout`: a conta fica livre na hora. Se o jogo fechar sem avisar (crash, queda de luz), a conta fica livre em até 30 s.
- **Uma sessão por conta**: cada login apaga os tokens antigos da conta, por isso um jogo velho com um token antigo já não consegue gravar nada (recebe 401). Se o sinal de vida receber 401, o jogo volta ao login com a mensagem *"Sua sessão terminou..."*.
- Dois logins ao mesmo tempo na conta livre: entra só um (a verificação e a marcação são feitas de uma vez, sem esperas no meio).
- Testado com as rotas reais e uma base falsa: 2º login recusado (409), logout libera, o token antigo deixa de valer, login de novo depois de 31 s sem sinal, recusa com a conta numa sala e, com dois logins simultâneos, só um passa.

### Onde o jogador aparece

- **Sala própria**: cada jogador tem a sua (criada no registo). O login devolve-a em `sala_propria`; uma conta antiga sem sala própria ganha uma nova no próximo login.
- **Ao logar, sem escolha de mundo**: o jogo entra direto na **última sala** onde o jogador estava ao sair (`ultima_sala`). Pode ser o mundo de outro jogador, a sala pública ou até a dungeon. Sem última sala, entra na própria.
  - A última sala é **gravada pelo servidor** quando o jogador entra numa sala pelo tempo real (o cliente não a consegue mudar). Por isso dá para voltar à dungeon ao logar, mesmo sem o passe da praça.
  - Se essa sala não abrir (apagada, sem acesso), vai para a sala própria; se nem essa abrir, volta ao login.
- **Ao morrer** fora da sala própria, volta para a **sala própria**, no ponto de spawn dela. Morrendo dentro de uma sala, volta ao **ponto de spawn** dessa sala. Volta **sem itens**: tudo fica num **cadáver** onde morreu (ver *Cadáver*).
- **Posição dentro da sala**: a última posição só vale para quem **saiu do jogo** ali dentro (logout, fechar a janela): ao logar, aparece onde estava. Quem **trocou de sala** (portal, ou morreu e foi para casa) tem a última posição daquela sala apagada, e ao voltar lá aparece no **ponto de spawn** da sala (`spawn_point`, ou perto do centro se não houver).
- **O corpo** de quem sai desaparece para os outros, e o de quem entra aparece.
- **Lugar ocupado ao aparecer**:
  - se há **blocos** onde o jogador estava, ele sobe até sair deles (como antes);
  - se há **outro jogador**, o jogo espera os outros chegarem pelo tempo real (até 4 s) e empurra o jogador para o lugar livre mais próximo: primeiro **acima**, em cima de algo na mesma coluna (até 12 blocos); sem nada acima, para o lado mais perto (até 4 blocos), com chão por baixo e espaço para o corpo. Aparece o aviso "O lugar estava ocupado: você foi empurrado". Os jogadores não colidem entre si, por isso subir no ar não serviria: o jogador cairia de volta no mesmo lugar.

### Como o mundo é gravado (servidor)

A ideia do jogo é que **tudo o que muda fica gravado**. Por isso o servidor (`server/src/storage.js`) usa um **diário em lote + fotos por chunk**:

1. Cada mudança do mundo (bloco posto/tirado/empurrado/caído, baús, cola, almas guardadas...) entra na hora no **diário** da sala, na memória do servidor.
2. A cada **0,25 s** as mudanças juntas de cada sala vão para o Mongo numa **só escrita** (coleção `journal`), confirmada pela maioria das réplicas do Atlas (`w: majority`). Se o servidor cair de repente, perde-se no máximo esse quarto de segundo.
3. A cada **60 s** (ou quando o diário de uma sala passa de 2000 mudanças, a sala fica vazia ou o servidor desliga) as chunks alteradas são gravadas inteiras em `chunks` (um documento por 16 x 16 colunas: `_id: "sala|cx|cz"`, `b: {"x,y,z": bloco}`) e o diário que já está nelas é apagado.
4. Ao abrir uma sala, o servidor lê as chunks e **reaplica o diário** por cima, por ordem: o mundo volta exatamente como estava.

Só se guarda o que **difere do mundo gerado**: um bloco que volta a ser o que era (pôr e tirar) deixa de ocupar espaço. Um mundo 40 x 40 tem no máximo 9 chunks (mais as de construções fora do mapa). O número de escritas não depende do número de jogadores: ~4 por segundo por servidor (diário) + 1 por chunk alterada por minuto.

Na **primeira vez** que esta versão do servidor arranca, os blocos do formato antigo (`room_blocks`, um documento por bloco) são convertidos para `chunks` sozinhos; a coleção antiga fica como cópia de segurança com o nome `room_blocks_antigo_AAAAMMDD` (pode apagá-la no Atlas depois de ver que está tudo certo).

### A física dos blocos corre no servidor

Online, quem decide o que acontece aos blocos é o **servidor**, não o jogo de cada jogador. O servidor Docker arranca um processo **Python** (`server/physics/worker.py`) que corre **a mesma física do jogo** (`engine/block_physics.py`, sem gráficos), uma por sala aberta, e também a **grama** a crescer e as **maçãs**.

- O jogo só **pede**: `hit` (golpe ou encontrão num bloco, com o dano e a direção), `place` (pôr), `remove` (tirar com `Q`) e `glue` (cola). Nada muda no ecrã até o servidor responder (uns milissegundos).
- O servidor decide (empurrar, tombar, cair, quebrar, void...), grava no diário (ver acima) e manda a todos: `blocks` (o estado final das células), `fields` (baús, cola, almas), `anim` (quedas e tombos, para cada jogo animar), `fx` (partículas). A quem bateu manda ainda `hit_result` (número de dano), `destroyed` (conteúdo do baú / almas do minoculus que quebrou) ou `reject` (não deu: o item volta para o inventário).
- O Node (`server/src/physics/bridge.js`) fala com o Python por linhas JSON; se o processo Python morrer, é reiniciado e as salas abertas são reabertas com o estado gravado.
- Abrir baús, guardar almas, verificar peso/estrutura antes do `Q` continuam no jogo, mas quem decide o que entra e sai é o servidor (ver "Segurança: o servidor é o dono").
- No **modo local** (sem servidor) a física continua a correr no próprio jogo, como antes.

### Segurança: o servidor é o dono

Online, **o jogador não consegue criar itens, blocos ou almas mexendo em ficheiros**. O servidor guarda o que cada jogador tem de verdade e confere tudo.

**Configurações só no servidor.** `itens.json`, `item_balancing.json`, `blocks.json`, `mobs.json`, `receitas.json` e `combate.json` são mandados ao jogo ao entrar (`GET /api/config`) e o jogo usa **só esses**, na memória (`game_config.py`). Mexer nos `.json` da pasta do jogo não muda nada online (nem itens, nem receitas, nem dano, nem blocos). Para mudar o jogo, mude os ficheiros do **servidor** e rode `docker compose up --build`. Offline (`"modo": "local"`) os ficheiros da pasta continuam a valer, como antes. `economia.json` (itens/almas iniciais, status base, modo de testes) **nunca** sai do servidor.

**O livro de cada jogador** (`server/physics/economy.py`, no processo Python da física; gravado em `economia` no registo do jogador). O servidor anota o que o jogador ganha e gasta:

| ganha (o servidor vê) | gasta (o servidor vê) |
|---|---|
| tirar um bloco com `Q` (o item/"drop" do bloco) | pôr um bloco (tem de ter o item; senão `reject`) |
| baú/minoculus que ele quebrou (conteúdo e almas) | cola (tem de ter cola) |
| item que um mob largou, almas do mob que matou | pôr itens/almas num baú ou minoculus do mundo |
| tirar itens/almas de um baú ou minoculus do mundo | materiais, almas e desgaste da pedra na transmutação |
| transmutação (com as receitas **do servidor**) | |
| itens e almas iniciais (`economia.json`, uma vez por conta) | |
| F6/F8 só no modo de testes | |

- **O inventário que o jogo grava** vai pelo tempo real (`{t:"inv"}`), na mesma fila dos pedidos de blocos (a ordem conta). O servidor compara com o livro: **o que tiver a mais é cortado** (do fim da mochila para o começo; durabilidade acima da verdadeira desce; almas a mais somem e os pontos que não cabem também) e o jogo recebe a correção (`inv_fix`, aviso *"O servidor corrigiu o inventário"*). O que tiver **a menos** (comeu, a arma gastou, morreu, inventário cheio) é aceite e o livro passa a ser esse.
- **Baús do mundo** (`{t:"chest"}`): ao fechar, o servidor compara com o que o baú tinha: o que entrou sai do livro (só entra o que o jogador tem), o que saiu entra no livro. **Baú na mão** (`{t:"icont"}`): igual, e só se o baú for mesmo dele. Pegar um baú cheio com `Q` manda o conteúdo para o servidor com o id do baú; pô-lo de volta devolve o conteúdo ao bloco. **Minoculus** (`{t:"bank"}`): guardar/tirar almas passa pelo servidor.
- **Rotas fechadas**: `PUT /api/players/me` só aceita a posição e a vida atual (status base iguais para todos, de `economia.json`; o resto — inventário, almas, itens iniciais, acesso aos mundos — é do servidor); `PATCH /api/rooms/:id` é ignorado (baús, almas, cola e ligações são do servidor); `POST /api/rooms/:id/blocks`, `PUT/DELETE /api/containers/:id` e a mensagem `{t:"block"}` (pôr qualquer bloco sem pagar) foram desligados; recomeçar um mundo só o dono (e nunca a sala pública).
- **Contas antigas**: na primeira vez que entram depois desta versão o livro é criado com o que já estava gravado (confia-se nos saves antigos). Daí em diante só vale o que o servidor viu.
- **Modo de testes**: `MODO_TESTES=1` no `.env` (ou `"testes": true` no `economia.json`) faz o servidor aceitar F6/F7 (±10 almas) e F8 (balde de água). Com `0`, as teclas mostram *"Teclas de teste desligadas neste servidor"*. Para jogadores de verdade ponha `MODO_TESTES=0` e tire as armas de teste de `itens_iniciais`.
- **Dano, velocidade e posição** (`server/src/guard.js`): o Python manda ao Node um *perfil* de cada jogador, feito só com o que o servidor sabe — força, vida e velocidade (status base + pontos de almas), as armas que ele **tem** no livro e o peso que leva:
  - **dano**: cada golpe num mob ou jogador é limitado ao máximo que a arma na mão (ou na 2ª mão) dá com a força e a carga dele, x o maior multiplicador do combate (finalizador x golpe carregado/contra-ataque, de `combate.json`). Arma que ele não tem = soco. Encontrões (`impacto`): no máximo a energia do corpo a toda a velocidade. Além disso, o **mesmo alvo** só leva golpes ao ritmo das armas dele (velocidade da arma x ritmo das almas; 2 seguidos de folga para a rede) — mais rápido que isso o golpe é recusado.
  - **distância**: a distância do golpe conta a partir da posição que o servidor aceitou (não a que o jogo diz).
  - **velocidade**: cada `{t:"state"}` só pode andar o que a velocidade dele deixa (o embalo de corrida vai até 8x a base, como no jogo; 1,6x com botas de voo), com folga de 12 blocos para empurrões e desencalhar. Subir: sem botas de voo, não se sobe mais de 10 blocos além do que se andou na horizontal (escadas e encostas sim, voar não).
  - **teletransporte**: ao entrar numa sala ou renascer, só vale aparecer perto da última posição gravada, do ponto de spawn ou do centro. Fora disso o servidor manda `{t:"pos_fix"}` e o jogo volta ao último lugar bom (*"O servidor corrigiu a sua posição"*); a posição errada não chega aos outros nem aos mobs.
  - **posição gravada**: a `last_position` da sala onde está é a que o servidor conferiu (o jogo só pode apagá-la, nunca inventá-la); `spawn_point` deixou de ser gravável pelo jogo.
- **O que ainda não está protegido**: atravessar paredes (o servidor confere a distância, não as colisões), e os golpes até ao máximo da arma (quem mudar o código consegue acertar sempre com o dano máximo). Mexer só nos ficheiros de configuração já não dá nada.
- Testado sem rede: o livro (tirar/pôr blocos, bloco sem item recusado, baú com 31 terras quando só tinha 1 → fica 1, baú cheio pego e posto de volta, inventário com 64 pedras e 99999 almas inventadas → cortado, transmutação, drops, baú na mão de outro recusado) e as configurações vindas do servidor com os `.json` da pasta apagados.

### Moderadores: Painel de administração

Projeto à parte (`painel_admin/`, em Python, entregue noutro zip) para moderadores: ver todas as salas, criar e fechar salas públicas e dungeons, interditar salas, controlar o spawn de mobs por sala e mexer no balanceamento dos itens. Ver o README dele.
- **Quem entra**: contas com `papel` "moderador" ou "admin", dado só pelo dono do servidor: `docker compose run --rm servidor node tools/moderador.js <usuario>` (`remover` tira; `--lista` mostra quem tem).
- **Servidor** (`server/src/admin.js`): rotas `/api/admin/*` com sessão própria (12 h, coleção `admin_sessions`); tudo fica no registo `admin_log`.
- **Salas fechadas/interditadas**: não aparecem no portal, não se entra (nem pelo `/join`, nem pelo tempo real, nem voltando à `ultima_sala` ao logar) e quem está lá recebe `{t:"kicked"}` e vai para a sala própria (ou para a praça). A praça pública não fecha.
- **Mobs por sala**: `mobs_admin` no registo da sala (`{desligado, tipos: {tipo: {max, recarga, desligado}}}`), lido pelo servidor dos mobs em `spawnSpec`.
- **Balanceamento**: o do painel fica no MongoDB (coleção `config`) e vale mais que o `item_balancing.json`; vai no `GET /api/config`, para o Python (dano máximo, durabilidade) e para os jogos ligados na hora (`{t:"config"}`: o jogo relê só o balanceamento).

### 1. Servidor (com Docker, em qualquer sistema)

1. Instale o [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Na raiz do projeto, copie `.env.example` para `.env` e ponha a sua string do MongoDB Atlas em `MONGODB_URI` (**nunca** a escreva no código nem a mande a ninguém: o `.env` está no `.gitignore` e no `.dockerignore`).
3. No Atlas, em *Network Access*, libere o IP de onde o servidor vai rodar.
4. `docker compose up --build`

Aparece `[db] ligado ao MongoDB`, `[http] Express em ...:3000` e `[enet] tempo real em udp://...:7777`. Teste: http://localhost:3000/api/health.

**Trazer os saves da 0.4** (rooms.json e players.json) para o MongoDB, uma vez:

```
docker compose run --rm -v "%cd%:/dados" servidor node tools/import_json.js /dados      (Windows, cmd)
docker compose run --rm -v "$(pwd):/dados" servidor node tools/import_json.js /dados    (Linux/macOS/PowerShell: ${PWD})
```

Sem Docker: Node 18+, `cd server`, `npm install`, `npm start` (o `.env` pode ficar na raiz ou em `server/`).

Para jogar com amigos noutra rede: o servidor precisa da porta **3000/TCP** e **7777/UDP** abertas (redirecionamento no roteador ou uma VPS), e cada cliente aponta o `servidor.json` para esse endereço.

### 2. Cliente (o jogo)

O ENet do Python (`pyenet`) tem versão pronta até o **Python 3.12**, por isso o cliente usa o 3.12 (o 3.14 não tem `pyenet`).

- **Windows**: abra o **`Instalar.exe`** (uma vez: instala o Python 3.12 só para o utilizador, se faltar, cria a pasta `.venv` e instala tudo; precisa de internet) e depois jogue sempre pelo **`Jogar.exe`** (abre o jogo sem janela preta; se ainda não instalou, ou o `requirements.txt` mudou, chama o `Instalar.exe` antes; se o jogo fechar com erro mostra a mensagem e grava `logs\jogo.log`). Não é preciso `cd` nem a linha de comandos. `instalar_cliente.bat` e `jogar.bat` continuam a funcionar.
  - Os `.exe` são feitos com Go a partir de `tools/launcher/main.go`: `python tools/compilar_launchers.py` (precisa do [Go](https://go.dev/dl/); compila para Windows de qualquer sistema).
  - **Pacote para os jogadores**: `python tools/empacotar_jogador.py` gera `dist/jogo_jogador.zip` só com o que o jogador precisa (os `.exe`, o código do cliente, texturas/modelos e o `servidor.json`), **sem** o servidor, o `.env`, o `economia.json` e as configurações do jogo (que vêm do servidor). Antes, ponha no `servidor.json` o endereço do seu servidor.
- **Linux/macOS**: `./instalar_cliente.sh` e `.venv/bin/python main.py`.

O cliente não roda em Docker: é um jogo com janela e OpenGL, e o Docker não mostra janelas de jogo de forma portátil (no Windows/macOS nem tem acesso à placa de vídeo). O Docker fica para o servidor, que é o que precisa de ser igual em todo o lado.

`servidor.json` escolhe o modo:

```json
{"modo": "online", "http": "http://localhost:3000", "enet_host": "localhost", "enet_port": 7777}
```

`"modo": "local"` volta ao modo da 0.4 (rooms.json/players.json, sem servidor, um jogador).

**Criar conta**: na tela de login, o botão **Criar conta** abre a tela de cadastro com **Usuário** (para entrar, sem espaços), **Nome do personagem** (o que os outros jogadores veem; vazio = o usuário), **Senha** (4+ caracteres) e **Confirmar senha**. Ao confirmar, o servidor cria o jogador e um primeiro mundo ("Mundo 1") e o jogo entra direto nele. **Voltar** ou `Esc` volta ao login. Funciona nos dois modos (online: `POST /api/register`; local: grava em `players.json`).

### Testes do servidor sem rede

`cd server && node tools/self_test.js` corre o mundo e os mobs sem banco nem ENet (persegue, ataca, bloqueia, nasce, grava).

### Itens iniciais

Todo jogador recebe os itens de `itens_iniciais` (e as almas de `almas_iniciais`) do **`economia.json` do servidor** na primeira vez que entra (uma vez só por item/conta: fica marcado em `itens_iniciais`/`almas_iniciais` no registo do jogador). Online quem dá é o servidor; `Game.STARTER_ITEMS` no `main.py` só vale no modo local.

Agora são só: minoculus, stand, baú de madeira, 4 pedras solares, **1 balde cheio de água**, a **pedra de transmutação** e um **isqueiro**. **Sem armas nem parafusos**: tudo se faz na pedra de transmutação (ver *Pedra de transmutação*), e mais pedras compram-se no minoculus. Contas que já receberam os itens antigos ficam com eles (os itens iniciais são dados uma vez **por item** e por conta: quem já jogou recebe agora o balde de água e o isqueiro, que são itens novos na lista).

### Caixas de colisão do jogador (cabeça e corpo)

O jogador tem duas caixas (`player_advanced/player_hitbox.py`): **cabeça** (o quarto de cima da altura) e **corpo** (o resto). O que acerta na **cabeça faz 1,5x o dano** (`HEAD_MULT`): a espada dos mobs (decidido no servidor, que testa a cabeça primeiro) e blocos que caem em cima de ti. O número de dano na cabeça sai em rosa e aparece "Na cabeça!". No Inspecionar (F4) as caixas aparecem: cabeça em rosa, corpo em verde (as tuas e as dos outros jogadores).

### PvP (jogador contra jogador)

No modo online dá para golpear outros jogadores: soco pela mira, espada pela caixa da lâmina e, em terceira pessoa, o alvo mais perto. Acertar a **cabeça** faz 1,5x o dano. O cliente de quem bate diz ao servidor quem e onde acertou (`{"t":"pvp"}` pelo ENet); o **servidor confere** (os dois na mesma sala, a menos de 7 blocos, no máximo um golpe a cada 0,12 s, dano até 100) e avisa a vítima, que aplica o dano com a armadura dela. Quem bate vê o número de dano por cima do outro e ele pisca em vermelho; quem leva vê o dano em vermelho (rosa na cabeça). Com a vida a zero, a vítima toca a animação de morte e volta ao início da sala.

### Combate em terceira pessoa

Em terceira pessoa (F5), o golpe vai no **alvo mais perto dentro do range de ataque** (mobs e blocos-alvo como o stand), mesmo que não esteja exatamente na mira: o corpo vira para ele quando o golpe começa. Socos acertam esse alvo no impacto do golpe; com espada, o corpo vira e a lâmina acerta pela própria caixa (na janela de impacto). Sem nenhum alvo no range, o golpe segue a mira como antes (blocos). Em primeira pessoa nada muda: vale a mira.

### Mãos em primeira pessoa

Em primeira pessoa (`client/first_person_view.py`):

- **Mãos vazias**: os dois antebraços aparecem nos cantos de baixo, em **pose de boxe** (punhos para cima, um pouco para dentro). Cada soco estica o braço do lado do golpe, a chegar à frente no impacto; em guarda os punhos sobem um pouco e a bloquear ficam à frente da cara; andar e correr balançam as mãos.
- **Segurando qualquer coisa** (blocos, espada, cola...): como antes, nas mãos do modelo do jogador, com o tamanho do `hand.scale` do `itens.json`. Ao trocar de mãos vazias para um item, os punhos descem para fora da tela (`EQUIP_TIME`) e o modelo com o item aparece; nunca aparecem os dois ao mesmo tempo.
- **Pegar e colocar blocos não se misturam**: enquanto a animação de pegar (Q) ainda corre não dá para colocar, e vice-versa (repetir a mesma ação continua livre).

As mãos vazias são desenhadas numa camada por cima do mundo, presa à câmera (nunca entram nas paredes); os braços são cópias das malhas do `player.gltf` (mesma pele). Ajustes no topo do ficheiro: `HAND_TIP`, `HAND_DIR`, `EQUIP_TIME`, `PUNCH_REACH`, `BOB_AMOUNT`.

### Sair do jogo (Esc)

**Esc** fecha a janela que estiver aberta (inventário, baú, portal). Sem nenhuma aberta, abre o menu de **Pausa** com **Continuar** e **Sair do jogo**. Sair grava tudo (posição, inventário, baús; no online, avisa o servidor e espera as gravações) e fecha a janela. Esc de novo volta ao jogo.

## Executar (modo local, como na 0.4)

```bash
python -m pip install -r requirements.txt
python main.py
```

`player_advanced/skin.py` gere a aparência e carrega `assets/models/player/player.gltf`; `player_advanced/armor.py` carrega e anima os overlays de armadura equipados. `player.py` liga os dois ao personagem. Há **quatro vistas**; `F5` ou `V` passa à seguinte (o nome aparece no meio da tela):

| Vista | Mira (bolinha) | Corpo | Para quê |
|---|---|---|---|
| **1a pessoa: livre** (a inicial) | **não** (a mira continua: o raio sai do meio da tela, como antes) | visível (braços, mãos, item; cabeça invisível) | jogar mais livre, sem o ponto no meio |
| **1a pessoa: construção** | sim | **invisível** (corpo, braços e item na mão) | construir: com um bloco na mão aparece o **holograma** dele (transparente, azulado, com contorno em arame) **onde ficaria** se clicasse, já virado como vai ficar (produced virado para o jogador, cilindros pela face, parafusos encaixados na cadeia, variantes pela face). Sem sítio válido (fora do alcance, ocupado, dentro do jogador) não aparece. |
| 3a pessoa de costas | não | visível | ver o modelo |
| 3a pessoa de frente | não | visível | ver o modelo de frente |

A mira tem **uma só regra**, verificada em todos os frames (`_sync_cursor` no `main.py`): aparece só na 1a pessoa de construção, viva, sem inventário, pausa ou portal abertos (antes cada janela guardava e repunha a mira, e às vezes as duas vistas ficavam com mira ou as duas sem). Na construção o corpo continua lá (animações, golpes e caixas de colisão funcionam igual), só não se desenha. O holograma mostra **só a superfície da frente** (como vidro): desenha-se em duas passagens, primeiro uma cópia invisível que só escreve a profundidade e depois a transparente, que só pinta onde está à frente (sem isto via-se a parte de trás através da da frente). O holograma usa as mesmas regras de pôr o bloco (`Interaction.plan_placement`, também usado por `place_block`) e só se refaz quando o sítio, o bloco ou a orientação mudam (`engine/placement_ghost.py`; `_update_ghost` no `main.py`, 20 vezes por segundo).

Para correr, mantenha `Ctrl` pressionado enquanto mantém `W`, ou toque duas vezes rapidamente em `W` (a corrida dura enquanto `W` estiver pressionado). Ao andar, braços e pernas balançam alternadamente; ao correr, o balanço fica mais amplo e rápido.

### Movimento e animações

| Ação | Tecla | Velocidade | Animação (`player.gltf`) |
|---|---|---|---|
| andar | `WASD` | base (`velocidade` em `properties_status`) | `walk` |
| correr | `Ctrl+W` ou duplo `W` | passa da base e **acelera enquanto houver estamina** (ver Aceleração) | `run` |
| agachar | `Shift` (segurar) | base −70% (`CROUCH_MULTIPLIER = 0.3`); não corre | parado: `crouch`; andando: pernas do `walk` (mais lento) + tronco/braços do `crouch` |
| pular / cair | `Espaço` | — | `jump` enquanto sobe, `fall` (loop) enquanto desce; sair de um degrau vai direto para `fall` |

**Aceleração.** O jogador arranca a 65% da velocidade base (`START_FRACTION`) e acelera 2 × base por segundo até a base (~0,2 s; andar). A velocidade base é **11** (`status_base.velocidade` em `economia.json`; era 10). **Correr** não tem teto: passa da base e continua a ganhar 0,35 × base por segundo **enquanto houver estamina** (correr gasta 7/s); com estamina infinita aceleraria para sempre, até bater em algo. Quando a estamina cai **abaixo de 35%**, o jogador perde velocidade (até 70% da base com a estamina a 0). Com 100 de estamina chega a ~40 em ~9 s; pontos de estamina (almas) deixam correr mais tempo, logo mais rápido. Soltar as teclas ou bater em algo volta à velocidade inicial. Há um teto de segurança (8 × base) e, com FPS baixo, o passo por frame é limitado para não atravessar blocos. `TEST_SPEED_BOOST` em `player.py` multiplica tudo para testes (agora `1`, normal).

**Força cinética (F4).** É a **energia** do encontrão: ½·m·v² com a massa de agora (corpo 40 kg + armadura + inventário) e a velocidade **real** de agora (já travada pelo peso que se leva); só **8%** passam para o alvo (`KINETIC_TRANSFER` em `physics_units.py`: o corpo amortece, não é um bloco rígido), em pontos = J / 100. Com 40 kg: a 15 unidades/s (7,5 m/s) 0,9; a 20 (10 m/s) 1,6; a 30 (15 m/s) 3,6 — só aí arrasta uma terra (mover 3,5); a 40 6,4. Com 80 kg a 20 dá 3,2 (ainda não move a terra). **Andar não dá encontrão**: só conta a **correr**, acima de 115% da velocidade base de andar (no máximo a cada 0,25 s), batendo **de frente**; aí a energia vai para o alvo e o jogador perde o embalo. **Mover um bloco com o corpo custa estamina**, a mesma de empurrar agachado (trabalho de arrastar 1 m / 8, mín. 10; uma terra ≈ 44) por cada bloco que sai do lugar; sem estamina (ou exausto) o encontrão bate e guarda dano mas **não move** o bloco, e aparece "Sem estamina para mover o bloco". Nos blocos, vai para **todos os blocos à frente na altura do corpo** (dos pés à cabeça, na largura do jogador; uma estrutura colada/árvore conta uma vez) e entra na física como um golpe: empurra se passar o **mover** e **fica guardada** no dano acumulado do bloco (10 s), somando com socos e espadadas. Nos mobs conta como `impacto` (o escudo não bloqueia); nos outros jogadores, como dano PvP no corpo. Bater no item que outro segura só tira o embalo. Quem bate também leva: a energia acima de 9 m/s, como numa queda (ver *Física*).

### Física: força (N), massa (kg) e energia (J)

O jogo usa unidades físicas de verdade, com uma lógica própria por cima (`physics_units.py`). A régua é **1 bloco = 1 m** (o jogador tem 1,5 m sem pontos de vida) e a gravidade é 9,8 m/s². No mundo do Ursina, 1 m são 2 unidades, por isso 20 unidades/s = 10 m/s.

| Grandeza | Unidade | Regra |
|---|---|---|
| **Força** do jogador | N | base **600 N**, **+60 N (+10%) por ponto** de almas (no Status aparece como o **dano do soco**: `soco 6,1`) |
| **Levantar** (Q) | kg | massa máx. = força / g (600 N levanta 61 kg; cada ponto +6 kg) |
| **Carga** (inventário + armadura) | kg | 100 kg + 12 kg por ponto de **vida** + 4 kg por ponto de força (`carry_capacity_kg`) |
| **Massa** de blocos/itens | kg | o `peso` de cada bloco (`blocks.json`) ou item (`item_balancing.json`): um número só para tudo |
| **Golpe** | pontos | regra do jogo: **cada 10 kg que você levanta = 1 de dano no soco**; a arma multiplica pelo `"fator"` dela em `item_balancing.json` (espada do esqueleto ×1,15, espada do herói ×1,8). Energia = dano × 100 J |
| **Dano** | pontos | **1 ponto = 100 J**. A vida também está em pontos: o jogador (10) aguenta 1000 J, o esqueleto (30) 3000 J. Os números na tela são pontos |
| **Bloco a cair/tombar, projétil** | J | ½·m·v², e **25%** passam para quem é atingido (`IMPACT_TRANSFER`) |
| **Encontrão** (correr contra algo) | J | ½·m·v² do jogador (corpo 40 kg + armadura + carga), 25% passam para o alvo |
| **Queda** e encontrão no próprio jogador | J | ½·m·v² acima do que as pernas aguentam (**9 m/s**); as pernas absorvem 90% do resto |
| **Empurrar** um bloco 1 m | N / J | força precisa = **atrito × m × g** (atrito 0,6; `"atrito"` ou `"empurrar"` em J por bloco). Um golpe empurra se a energia dele (J) pagar o arrasto de 1 m |
| **Quebrar** | J | `"tenacidade"` do material; os golpes somam (10 s) |
| **Desabamento** | N | carga = Σ m·g por cima; `"compressao"` (N que aguenta antes de esmagar) e `"estabilidade"` (N: pilha solta tomba acima de 75%; a cola aguenta um momento de até isto em N·m) |

**Levantar e bater andam juntos.** O que você levanta e o dano que dá vêm da mesma força, numa relação fixa: **10 kg = 1 de dano** no soco (`KG_PER_DAMAGE`), e a arma multiplica.

**Dano de um golpe** (`physics_units.weapon_damage`, `Player.damage_with`) = **força do jogador** (kg que levanta / 10) × **multiplicador da arma** (`fator`) × **fator da carga**. O peso **não mexe na força** (nem no que se levanta): só no **dano efetivo** dos golpes (soco e armas). A carga é o que leva (inventário + armadura) comparado com uma **capacidade fixa** (100 kg com a vida base, mais 12 kg por ponto de vida; `damage_load_capacity_kg`) — **subir força não apaga a penalidade** (antes subir força fazia o "-60% peso" sumir, o que só ajudava quem já era forte): até 70% não muda nada; a partir de 70% o golpe perde 1% por cada 1% a mais, até no máximo −60% (levando 100%: −30%; 130% ou mais: −60%). Números em `LOAD_DAMAGE_FROM`/`_SLOPE`/`_MIN`. O painel Status mostra a **Força** como o soco sem peso (`soco 6,1 (+pontos)`) e uma linha nova **Dano (golpe)** com o dano efetivo da mão/arma de agora e o desconto do peso (`6,1 · -20% peso`); o servidor usa a mesma conta no limite de dano. Além disso, o tooltip das armas mostra o **Dano** já com a sua força e a carga de agora, e cada arma tem também a sua **Velocidade de ataque** própria (`velocidade` em `item_balancing.json`, multiplicada pela do jogador; ex.: adaga x1,3, machadinha x0,9). Com a vel. de ataque do jogador, no máximo 3x.

| Força | Levanta | Soco | Espada do esqueleto | Espada do herói |
|---|---|---|---|---|
| 600 N (base) | 61 kg | 6,1 | 7 | 11 |
| 2100 N (+5 pontos) | 214 kg | 21,4 | 24,6 | 38,6 |
| 3600 N (+10 pontos) | 367 kg | 36,7 | 42,2 | 66,1 |
| 6600 N (+20 pontos) | 673 kg | 67,3 | 77,4 | 121,2 |

Cada ponto de força soma **+60 N (+10% da base)**: +6 kg para levantar e **+0,6 de dano no soco** (+1,1 na espada do herói). Antes eram +300 N (+50% por ponto, +3 de dano), forte demais. Os pontos já postos passam a valer o novo número.

A **força base é sempre 600 N** (só os pontos de almas somam). Valores antigos gravados na conta são ignorados.

**Velocidade conta ao quadrado.** Quedas e impactos altos ferem muito mais do que os baixos. Carregar peso (armadura, inventário cheio) também faz a sua queda e o seu encontrão serem mais fortes.

| Situação | Dano |
|---|---|
| Terra (60 kg) caindo 1 / 2 / 4 blocos na cabeça | ~2,3 / 4,5 / 9 |
| Pedra (25 kg) caindo 4 blocos | ~3,8 |
| Jogador caindo a 13,5 / 18 / 22,5 m/s com 40 kg | 2 / 4,9 / 8,5 |
| A mesma queda com 80 kg (armadura + carga) | 4 / 9,7 / 17 |

**Empurrar agachado.** Agachado (`Shift`) e andando **contra um bloco solto encostado**, o jogador empurra-o 1 casa (e a fila de blocos soltos à frente), **sem golpe e sem lhe fazer dano**, se a força do empurrão pagar o atrito da fila (atrito × m × g de cada bloco, mais o peso que tiverem em cima). **Força do empurrão = força de dano + força cinética**: a energia do soco (dano do soco × 100 J, sem a arma) mais a **energia cinética inteira** do corpo naquele momento (½·m·v², 100%: empurrar devagar e encostado transfere quase tudo, ao contrário do encontrão, que só passa 8%; agachado a ~1,5 m/s, 40 kg dão ~45 J). Com 600 N: soco 612 + cinética. Se não chegar, aparece quanto precisa e quanto você tem (`soco X + cinética Y`).
- **Estamina:** gasta muita, o trabalho de arrastar 1 m (J) / 8, no mínimo 10. Por exemplo terra 44, fragmento solar 59, baú de madeira 22.
- **Ritmo:** um empurrão a cada 0,45 s enquanto continua a andar contra o bloco.
- **Quando não dá:** sem força aparece *"Pesado demais para empurrar: precisa 706 N, você tem 600 N"*; sem estamina, *"Sem estamina para empurrar"*.
- **O que não sai:** blocos colados, chão natural, pedra adormecida (que conta como chão), modelos maiores que 1 bloco e filas que batem em algo fixo.
- Online, quem decide é o servidor (pedido `push`).

| Bloco | Massa | Empurrar (N) | Tenacidade (J) | Estabilidade (N) | Compressão (N) |
|---|---|---|---|---|---|
| terra / grama | 60 kg | 353 | 2000 | 3528 | 11760 |
| madeira (`wood`) | 50 kg | 294 | 3000 | 4704 | 17640 |
| folhas | 5 kg | 29 | 400 | 588 | 2352 |
| pedra | 25 kg | 147 | 8000 | 4704 | 47040 |
| fragmento solar | 80 kg | 470 | 3000 | 4704 | 17640 |
| pedra solar | 40 kg | 235 | 2500 | 3528 | 14700 |
| baú de madeira | 30 kg | 176 | 2500 | 3528 | 14700 |
| baú grande | 120 kg | 706 | 8000 | 11760 | 47040 |
| banco | 15 kg | 88 | 1200 | 1764 | 7056 |
| minoculus | 20 kg | 118 | inquebrável | 2352 | — |
| pedra dura | 250 kg | (fixa) | 200000 | — | — |

Os limites do desabamento foram convertidos dos antigos usando o peso de 1 terra (588 N) como referência, por isso os exemplos continuam iguais (5 terras soltas em pé, a 6.ª derruba; viga colada de 3 aguenta, a 4.ª cai). Mudam só as pilhas de materiais com massa real diferente: madeira 8 (era 7), pedra 15 (era 21), fragmento 5 (era 7), folhas 10 (era 4). Testado com a física do servidor (`server/physics`): pilhas, viga, empurrar e golpes.

### Peso dos blocos e dos itens

Cada bloco tem `"peso"` em kg em `blocks.json` (`block_properties.peso`): terra 60, madeira 50, folhas 5, baú 30, minoculus 20, banco 15, stand 40, pedra dura 250.

- **Pegar com `Q`:** o peso a levantar é o do bloco **mais tudo o que está apoiado em cima dele** (pilhas, a árvore inteira, construções coladas, o conteúdo de baús). Só é calculado na hora de pegar. O jogador levanta **força / g**: 600 N = 61 kg, e cada ponto de força (+60 N) soma ~6 kg. Assim já não dá para tirar a terra de baixo de uma árvore sem força: terra + 3 troncos + folhas ≈ 220 kg, o que pede 2160 N (~26 pontos). Acima do limite aparece "Pesado demais: 220 kg (você levanta até 61 kg com 600 N - suba a força)".
- **Peso honesto**: no inventário cada item pesa **o mesmo que no mundo** — o `Peso:` do tooltip é o que se leva e o que se levanta com `Q` (antes a mochila contava 1/4 e o tooltip o peso inteiro, o que confundia). Em troca, **os blocos pesam 4x menos** que antes (`blocks.json`: terra/grama 15 kg, tronco 12,5, pedra 6,25, pedra solar 10, baú 7,5, baú grande 30, folhas 1,25; a pedra dura do fundo continua 250). 64 terras = 960 kg. Os itens iniciais pesam 65 kg.
- **O peso do corpo** (40 kg com 1,5 blocos de altura; cresce com a altura) conta, com a armadura e o que se leva, nas **quedas**, nos **encontrões** e ao **empurrar** blocos (`collision_mass`), e aparece no Status: `Peso: corpo 40 + leva 10 / 100 kg`. Não conta para a carga: a capacidade é o que se aguenta **a mais** do próprio corpo. Itens que não são blocos usam o `peso` de `item_balancing.json`. Um **baú** pesa ainda o que leva dentro (`data.peso_conteudo`, atualizado ao fechá-lo); baús no mundo contam o conteúdo ao serem pegos.
- **O peso trava a velocidade, aos poucos** (`Player.load_speed_factor`): `velocidade × (1 − 0,2 × (carga/capacidade)^1,5)`, no mínimo 35%. Quase nada com pouca carga e cada vez mais ao passar da capacidade: 25% da carga → 97%; 50% → 93%; 75% → 87%; **100% → 80%**; 150% → 63%; 200% → 43%; 230% ou mais → 35%. Os itens iniciais (65% da carga) → 90%. Vale também a correr e com pontos de velocidade. (No início era `1 / (1 + carga/capacidade)`, muito punitivo: 50% com a carga cheia; depois ficou sem lentidão até 100%.) Números em `LOAD_SLOW` / `LOAD_CURVE` / `LOAD_MIN_SPEED`.
- **A capacidade de carga vem sobretudo da vida** (`Player.carry_capacity`, `physics_units.carry_capacity_kg`): 100 kg com a vida base (10) + **12 kg por ponto de vida** + 4 kg por ponto de força. Ex.: +10 de vida → 220 kg; +10 de força → 140 kg; +10 de cada → 260 kg. A força continua a decidir o que se **levanta** (pegar um bloco com Q: força / g), o que se **empurra** e o **dano**. Antes a força fazia tudo isto e ainda a carga, e era de longe o melhor atributo.
- **Queda com carga**: a massa da queda conta a carga, mas pernas fortes aguentam mais (o dano divide-se por força / 600 N). Com força base e sem carga, uma queda a 13,5 m/s tira 2; com 61 kg às costas, 5,1. Com 18900 N e 2400 kg às costas, 3,9.
- O **peso do jogador** = 40 kg + armadura vestida + inventário, e entra na **força cinética**: carregar muita coisa bate com mais força.

### Status no inventário e almas

Com o inventário aberto (`P`), à esquerda aparece o painel **Status**: nome, altura, força (mostrada como o **dano de um soco** sem o peso: `soco 6,1  (+pontos)`; os Newtons ficam no F4), **dano (golpe)** (o dano efetivo da mão/arma de agora, com o desconto do peso: `6,1 · -20% peso`), velocidade (com quanto o peso a trava: `10  · -18% peso`), estamina, levanta (`blocos até 61 kg`: o bloco mais pesado que a força deixa pegar), peso (`leva 13 / 61 kg`: o que leva e a capacidade de carga), velocidade de ataque (`x1,35  (+5)`), armadura e **almas** (mais os pontos livres e quantas almas faltam para o próximo ponto).

**Almas** sobem os outros status. Os botões **+** ao lado de Vida máx., Força, Velocidade, Estamina e Vel. de ataque põem um ponto (cada ponto: vida máxima +1 (e altura, e **slots do inventário**: ver abaixo), força +60 N, velocidade +0,5, estamina máxima +10, velocidade de ataque +7% em todos os golpes (soco e armas); `BONUS_PER_POINT` em `player_advanced/souls.py`). O painel mostra também o **peso** (com o que vai no inventário) e quanto o jogador **levanta**.

**Slots do inventário pela vida.** O inventário começa com **8 slots** (só a hotbar) e cada **10 pontos de vida** liberam **mais 8** (uma fila, de baixo para cima): 10 pontos → 16, 20 → 24, 30 → 32 (o máximo). As filas fechadas **não aparecem** (o equipamento e o baú aberto descem para cima da última fila à vista); ao subir a vida, os slots novos **aparecem um a um, a crescer**, da próxima vez que se abre o inventário. Não recebem itens enquanto fechados (apanhar, transmutar, comprar e arrastar só usam slots abertos); um slot fechado que ainda tenha um item (inventário antigo) aparece escuro até se tirar esse item. `VIDA_PER_ROW` em `player_advanced/inventory.py`. **Gastar almas de propósito** (transmutar, comprar no minoculus, guardar almas nele) que tiraria pontos de vida e **fecharia filas com itens** não é feito: aparece *"Não dá: gastar N almas tira X ponto(s) e fecha slots com itens. Esvazie a fila de cima do inventário primeiro"* (`souls_cost_blocked` no `main.py`). Morrer não é de propósito: aí os itens vão todos para o cadáver.

**Descanso.** Parado **4 s** sem fazer nada (sem andar, sem teclas de movimento, cliques, golpes ou ações; olhar à volta não conta), as **pernas voltam suavemente à pose de repouso** do modelo (direitas, juntas; ~0,35 s). Os clipes de parado ("idle", segurar, guarda) não mexem nas pernas, por isso elas ficavam presas no último passo da caminhada, mesmo de mãos vazias. Segurar e a guarda continuam iguais nos braços/tronco. Qualquer movimento volta ao normal. `Player.REST_AFTER`, `PlayerSkin._apply_leg_rest`.

**Balde na mão**: pende pela **asa** na mão direita (o modelo tem a origem no fundo, por isso antes ficava "em pé" na palma, enorme, à frente do corpo): `hand` em `itens.json` com `scale` 0,55, `position` [0, −0,5, 0] (a asa no punho) e `rotation` [0, 90, 0] (asa para a frente).

**Colisão com troncos.** O topo de um bloco só conta como **chão** se estiver descoberto (sem outro bloco colado por cima), e a base só conta como **teto** se não tiver um bloco colado por baixo. Antes, ao pular encostado a um tronco (cilindro: o empurrão é redondo e a pegada dos pés quadrada), a junta entre dois blocos do tronco servia de chão e o jogador ficava "pendurado" a meio da árvore (`ground_surface` / `ceiling_limit` em `player_advanced/building.py`).

**Altura pela vida.** A cada **3 pontos de vida** o jogador fica **0,1 bloco mais alto** (**1,5 blocos** sem pontos — era 1,8; 3 pontos 1,6; 6 pontos 1,7; 15 pontos 2,0), até **+1 bloco** (2,5 blocos, com 30 pontos). Os olhos ficam a 90% da altura. Crescem juntos o corpo (colisão e tetos), os olhos (câmera), o modelo e as caixas de golpe (cabeça/corpo). O painel Status mostra a altura. Online a altura vai no estado do jogador (`altura`), por isso os outros o veem e acertam com o tamanho certo. Números em `Player.HEIGHT_EVERY` / `HEIGHT_STEP` / `HEIGHT_MAX` (`player.py`).

- As almas **não são gastas**: é preciso **carregá-las**. Os pontos que dá para ter dependem das almas que se tem: os primeiros 100 valem 1 ponto a cada 10 almas; de 100 a 200, 1 a cada 20; de 200 a 300, 1 a cada 30, e assim por diante (100 almas = 10 pontos, 200 = 15, 300 = 18, 1000 = 29).
- Se as almas baixarem, os pontos que deixam de caber **somem**, começando pelos últimos que foram postos.
- Ganham-se almas a derrotar mobs (25 por mob; `"almas"` no tipo em `mobs.json` muda). Ao **morrer** perde-se metade (`LOST_ON_DEATH`).
- **Testes:** cada conta recebe **500 000 almas** uma vez (`STARTER_SOULS` em `main.py`; 0 desliga), e `F6` dá 10 almas, `F7` tira 10.
- Itens iniciais (uma vez por conta, `STARTER_ITEMS`): minoculus-grump, stand e baú de madeira.
- Gravado na conta em `"almas": {"total": 140, "pontos": ["forca", ...]}`; os status base em `properties_status` não mudam.
- **Minoculus guarda almas.** Botão direito num minoculus (no mundo, ou o seu na mão sem bloco na mira) abre o portal com a secção **Almas guardadas**: guardar/retirar 100, 1000 ou tudo. Almas guardadas **não contam** para os pontos (é preciso carregá-las), mas **não se perdem ao morrer**. Cada minoculus tem as suas (não acumulam no inventário): pegá-lo com `Q` leva as almas dentro (`data.almas`), colocá-lo devolve-as ao bloco (sala: `soul_banks`), e se ele quebrar as almas vão para quem está ali. No minoculus na mão de outro jogador só se usa o portal. Online, as almas de um minoculus no mundo são lidas ao entrar na sala: se dois jogadores mexerem no mesmo ao mesmo tempo, vale a última gravação.

**Corpo e cabeça como no Minecraft.** A câmera e a mira ficam sempre para onde se olha (o corpo nunca as puxa), em 1ª e 3ª pessoa: na vista de costas a câmera fica atrás da cabeça e na de frente diante do rosto. A **cabeça** vira e inclina para olhar para onde a câmera aponta; o **corpo** vai atrás aos poucos (`Player._update_body_turn`, a mesma regra do `LivingEntity` do Minecraft):

- andando, o corpo vira para a direção do movimento, mas nunca a mais de 75° da cabeça e, acima de 50°, é puxado de volta: de lado (`A`/`D`) fica a ~53°, na diagonal a 45°;
- para trás (`S`, ou mais de 95° da cabeça) anda de costas: o corpo fica de frente e o `walk` toca ao contrário;
- parado, o corpo só se mexe quando a cabeça passa de 50° (girar o mouse pouco não o vira);
- a golpear, o corpo alinha com a cabeça.

Vale também agachado e correndo. Os números estão no topo da classe `Player` (`BODY_MAX_ANGLE`, `BODY_PULL_ANGLE`, `BODY_TURN_PER_TICK`...). A sensibilidade do mouse é `Player.MOUSE_SENSITIVITY = (25, 25)` (antes 60; padrão do Ursina 40).

Ao agachar a câmera desce `CROUCH_EYE_DROP` (0,35) suavemente, e a mira acompanha. Ao andar agachado, o `Player Wide` não é abaixado para os pés não afundarem no chão com as pernas do `walk`.

A verificação de apoio do jogador também consulta os blocos da sala, para evitar que ele atravesse o chão se o collider visual de um chunk ainda não estiver disponível.

Ao abrir o inventário com `P`, há quatro slots de equipamento acima da grelha normal: capacete, peitoral, pernas e botas. Arraste uma peça compatível para equipá-la, use `Shift+clique` numa peça do inventário para equipá-la rapidamente ou numa peça vestida para devolvê-la. Os nomes reconhecidos são `elmo 1`, `peitoral 1`, `calça 1`, `bota 1` e as versões com `2`; os modelos são lidos do respetivo campo `model` em `itens.json`. Para facilitar o teste, as oito peças são colocadas no inventário inicial.

`items.py` carrega os itens de `itens.json`, enquanto os blocos continuam a ser carregados de `blocks.json`; ambos usam o mesmo inventário. `corporeal: "none"` significa que o item não tem forma física na sala. Os ícones de `itens.json` são procurados em `assets/icons/`; se o ficheiro PNG ainda não existir, o inventário mostra uma cor de reserva.

## Item na mão

**Blocos na mão maiores**: os blocos ficaram 1,5x maiores na mão (`hand.scale` 0,4 → 0,6 e `hand.position` z −0,12 → −0,18 em `itens.json`; o balde 0,8 → 1,2). O item selecionado na hotbar aparece como modelo 3D **entre as duas mãos** do jogador, em qualquer item do `itens.json` (blocos, armaduras e outros). O modelo fica preso às pontas dos antebraços do `player.gltf` (`player_advanced/held_item.py`), por isso acompanha as animações dos braços: `hold_block` (segurar) e `place_block` (colocar). Ao andar, correr ou pular os braços continuam na pose de segurar, para o item não balançar de lado. É visível em primeira pessoa (olhando para baixo) e na terceira (`F5`/`V`).

O modelo vem de, por ordem: `hand.model` do item; o glTF do bloco (ex.: baú); o `model` do item (armaduras); o cubo texturizado do bloco; o ícone, como carta 3D.

### Ajustar o item: `itens.json` → `hand`

```json
"hand": {
  "model": null,
  "position": [0, 0, 0.12],
  "rotation": [0, 0, 0],
  "scale": 0.4
}
```

- `scale` (aumentar): o modelo é normalizado para a maior dimensão valer 1, então `0.4` é o tamanho final. Aceita número ou `[x, y, z]`.
- `position` (mover): a partir do ponto exatamente entre as duas mãos, nos eixos do corpo: `x` direita, `y` cima, `z` frente.
- `rotation` (girar): graus em `x`, `y`, `z`.
- `model`: `null`, `"block"` ou `"item"` usa o modelo do próprio item; um caminho `.gltf`/`.glb` usa outro modelo só na mão.

**Recarga ao vivo:** com o jogo aberto, edite e guarde o `itens.json`; a mão atualiza em até 0,5 s, sem reiniciar. Se o JSON estiver com erro, o jogo mantém a versão anterior e avisa no console.

## Câmera em primeira pessoa e blocos

O corpo para a 0,45 da parede, mas a câmera fica 0,5 à frente do centro, então encostar num bloco a deixava dentro dele e via-se através (troncos `wood` incluídos). `Player._first_person_clear_forward` agora recua a câmera o necessário (`FIRST_PERSON_CLEARANCE` define os pontos verificados, que cobrem o plano de corte próximo) e a devolve suavemente ao se afastar.

## Combate (guarda, golpes, combos, bloqueio)

O combate do jogador segue o guia do `player.bbmodel` novo (41 animações: guardas, golpes de cada arma, bloqueios, `combat_damage`, `death`, `attack_running`). A lógica fica em `player_advanced/combat.py` (sem Ursina, testável sozinha) e **todos os números em `combate.json`** (recarrega ao guardar, com o jogo aberto).

**Três estados (+ bloqueio, reação e morte)**

```
[RELAXADO] idle/walk/run --(ataca, leva dano, inimigo perto)--> [EM GUARDA] combat_idle_<arma>
[EM GUARDA] --clique--> [ATACANDO] attack_<arma> --(o golpe acaba)--> [EM GUARDA]
[EM GUARDA] --botão direito carregado--> [BLOQUEANDO] block_<arma>
```

- Depois de um golpe o jogador volta **sempre à guarda**, nunca direto ao relaxado. Sem atacar, bloquear, levar dano nem ter um mob hostil a menos de 5 blocos durante `guarda_segundos` (4 s), relaxa.
- **Transições suaves** (`transicoes`): relaxado→guarda 0,15 s, guarda→relaxado 0,3 s, golpe→golpe (combo) 0,08 s, entrar/sair do bloqueio 0,1/0,15 s. A pose antiga mistura-se com a nova (todas as trocas de animação do jogador ficaram suaves, também andar/parar/pular).
- Do relaxado, o primeiro golpe espera os 0,15 s da guarda (o jogador "prepara-se") e só depois sai, como no roteiro do guia.

**Anatomia de um golpe**: preparação (sem dano; **levar dano aqui cancela o golpe**) → **impacto** (o dano sai aqui: a arma só acerta numa janela de `janela_impacto` = 0,12 s à volta do instante de impacto do clipe) → recuperação. A partir da **janela de combo** o próximo clique é aceito; um clique antes disso fica **guardado** (buffer de 1) e o golpe seguinte sai sozinho quando a janela abre — fluido e sem spam. Os tempos de cada clipe (`golpes` em `combate.json`) são os da tabela do guia:

| Clipe | Duração | Impacto | Combo |
|---|---|---|---|
| punch_right / punch_left | 0,75 | 0,25 | 0,45 |
| attack_1h_right / _left | 1,10 | 0,28 | 0,55 |
| attack_1h_backhand | 1,00 | 0,32 | 0,55 |
| attack_2h | 1,60 | 0,57 | 0,95 |
| attack_2h_horizontal | 1,40 | 0,58 | 0,95 |
| attack_polearm | 1,37 | 0,38 | 0,75 |
| attack_shield | 0,90 | 0,25 | 0,45 |
| attack_running | 0,70 | 0,26 | 0,45 |

**Combos por arma** (`estilos`; o estilo sai da arma na hotbar + o slot **2ª mão**):

| Estilo | Quando | Guarda / bloqueio | Combo | Extra |
|---|---|---|---|---|
| desarmado | mãos vazias | combat_idle_unarmed / block_unarmed | soco dir. → soco esq. → soco dir. | 3º +50% |
| 1h | espada (arma `"estilo": "1h"`) | combat_idle_1h / block_1h | corte dir. → corte de volta → corte dir. | 3º +50% |
| dual | arma 1h + arma 1h na 2ª mão (ex.: duas adagas) | combat_idle_1h / block_1h | dir. → esq. → de volta → esq. | combo mais cedo (0,5 s); a mão esquerda bate com a arma dela |
| 1h_esquerda | mão direita **vazia** + arma 1h **só na 2ª mão** (ex.: uma adaga) | combat_idle_1h / block_1h | esq. → esq. → esq. (finalizador) | ataca **com a arma** (dano, alcance, velocidade e caixa de colisão da adaga), só que com a mão esquerda; gasta a durabilidade dela. Sem golpe a correr (o clipe é da mão direita). Antes dava soco. |
| espada_escudo | arma 1h + escudo na 2ª mão | combat_idle_1h / block_shield | dir. → de volta → **empurrão com escudo** | contra-ataque |
| escudo | só o escudo (hotbar ou 2ª mão) | combat_idle_unarmed / block_shield | soco → empurrão | contra-ataque |
| 2h | arma `"estilo": "2h"` | combat_idle_2h / block_2h | varrida → vertical | **segurar e soltar** (0,45 s): só o vertical, x1,6 |
| polearm | arma `"estilo": "polearm"` | combat_idle_polearm / block_2h | estocada → estocada (1,1x) → varrida | |

- **Velocidade dos golpes** = `velocidade` da arma (`item_balancing.json`, 1 = o tempo do guia; espada do esqueleto 1,15) x a do jogador (`attack_speed` / 2: **+7% por ponto** de almas), no máximo **3x**. Vale para **todas as armas e os socos**: a animação inteira fica mais rápida (preparação, impacto, janela de combo e recuperação), e também a entrada em guarda antes do 1º golpe e as transições entre golpes. Com a espada, em 5 s a clicar sem parar: 0 pontos 9 golpes, 5 pontos 12, 10 pontos 15, 20 pontos 21, 29 pontos (o máximo, 3x) 27.
- **Dano de cada golpe** = soco da força (kg que levanta / 10) x fator da mão que bate (arma da direita, arma da esquerda no dual, escudo `"fator": 0.8` no empurrão) x finalizador/correndo/contra-ataque/carregado.
- **Estamina** por golpe (`estamina` do estilo: soco 3, espada 7, duas mãos 12); sem estamina não se ataca.
- **Atacar em movimento**: parado, o golpe usa o corpo todo (passo e peso); **andando**, as pernas continuam o walk e só o tronco/braços fazem o golpe (o mesmo para a guarda e o bloqueio: a pose `_upper` do guia é feita no código, aplicando só os ossos do tronco). **Correndo**, o clique faz `attack_running` por cima do run (+25% de dano, +6 de estamina) e o jogador continua a correr.
- **Contra-ataque** (escudo): a bloquear com escudo, clicar faz o empurrão com escudo; o golpe seguinte (clicando) faz +50%. O empurrão nos mobs (`"empurrao"`, no servidor) **não gasta o escudo do mob: abre-lhe a defesa** (`block_destroy`, e o golpe seguinte leva o `broken_damage_mult` dele) e, sem escudo levantado, atordoa e afasta 1,5 blocos.
- Em terceira pessoa o corpo vira para o alvo mais perto quando o golpe começa; os socos e o empurrão acertam no impacto (alvo mais perto, senão a mira); as armas acertam pela caixa delas durante a janela de impacto.

**A defesa é prioritária e imediata**: o botão direito **corta o golpe que estiver a sair** (menos o instante do impacto e o contra-ataque), o golpe carregado e, passados 0,15 s, a reação ao dano; a pose entra em 0,05 s e conta logo como a defender (o escudo em 0,04 s). `bloqueio.trava_reacao` e `transicoes.entrar_bloqueio` em `combate.json`.

**Bloqueio** (segurar o **botão direito** com arma, escudo ou mãos vazias; com um bloco/ferramenta na mão o botão direito continua a colocar/usar): entra em 0,1 s, anda a metade da velocidade (`bloqueio.lentidao`), não corre e não ataca (exceto o contra-ataque com escudo). Um golpe **de frente** (75°) não faz `combat_damage`:
- **com uma arma (sem escudo)**: a arma **aguenta o golpe todo** (o jogador não perde vida), mas gasta **6x o dano em durabilidade** (`bloqueio.desgaste_arma`; era 2x: defender com arma de ataque gasta-a depressa — uma adaga de 400 aguenta ~7 defesas de 10 de dano, a espada do herói ~25; para defender muito, use um escudo) e 2 de estamina por ponto (`estamina_por_dano_arma`). Se a arma não tiver durabilidade para tanto, segura o que der, quebra, e o resto passa. Sem estamina, **a defesa quebra**;
- **de mãos vazias**: tira 30% do dano (`reducao_bloqueio` do desarmado) e gasta 4 de estamina por ponto bloqueado;
- **com escudo**: o escudo vai à frente com o `block_shield` e **só bloqueia o que bate nele** (tudo, gastando a durabilidade do escudo), como antes — ver "Escudo do jogador".

**Reações e prioridades**: `death` > rolar > `combat_damage` > bloqueio > golpe > guarda > idle/walk/run.
- Levar dano (golpes, projéteis, explosões, PvP; não quedas, colisões, contacto) toca `combat_damage` e volta à guarda. Interrompe a preparação e a recuperação, **não** interrompe o impacto nem o bloqueio. Durante 0,4 s (`reacao.trava`) não se ataca, bloqueia nem rola; depois o resto da animação pode ser cortado por um golpe.
- **Rolar** (Shift + andar + Espaço) cancela a recuperação dos golpes (a partir da janela de combo) e o bloqueio; não cancela a preparação nem o impacto.
- **Morte**: toca `death` (o corpo cai e fica parado), sem controlo; os mobs deixam de o atacar. Passados `morte.segundos` (1,6 s) volta ao início da sala (ou à sala própria), com tudo cheio.

**Estilo do corpo**: `"estilo_corpo": "f"` em `combate.json` usa as versões `_f` (postura feminina, mesmos golpes) de todos os clipes que as têm.

**Outros jogadores** veem a guarda, o bloqueio, os golpes (do corpo todo ou só do tronco), as reações, a morte e o item da mão esquerda (o estado leva `stance`, `dead` e `offhand`).

**F4** mostra o estilo, o estado e a fase do golpe (`1h · ataque · attack_1h_backhand (impacto) x1`). Testes da máquina de estados (o roteiro do guia, buffer, cancelamentos, rolar, contra-ataque, 2 mãos, dual, FPS baixo): passaram.

### Cadáver

Ao morrer, o jogador deixa **o corpo** onde caiu (o modelo dele deitado, na pose final da animação `death`, com *"Cadáver de Nome"* por cima) com **todos os itens**: inventário, equipamento (armadura, 2ª mão) e o que tinha no cursor. Ele **volta sem nada** (as almas seguem a regra de antes: perde metade). Os itens iniciais não voltam.

- **Abrir**: botão direito a mirar o cadáver (até 6 blocos) abre-o como um baú (até 40 slots), com o tempo que falta no título. Qualquer jogador pode tirar (ou pôr) itens.
- **Some sozinho**: **10 min** se ninguém tirar nada; **1 min** depois de tirarem o primeiro item (*"O cadáver some em 60 s"*). O que ficar dentro perde-se.
- **Online** quem decide é o servidor: ao morrer o jogo manda `{t:"death"}`; o worker Python passa **tudo o que o livro do jogador tem** para o cadáver (o livro fica vazio) e o cadáver vai no campo **`corpos`** da sala (gravado no MongoDB e mandado a todos). Fechar o cadáver manda `{t:"corpse", id, entries}`, conferido como um baú (`economy.transfer`: só se tira o que lá está) e só a até 6 blocos (`corpse_fix` se não). A posição do cadáver é a última posição boa que o servidor conhece. Sem servidor o jogo faz o mesmo sozinho.
- Código: `engine/corpses.py` (estado e tempos), `engine/corpse_view.py` (desenho e mira), `_drop_corpse` / `_try_open_corpse` no `main.py`, `_death` / `_corpse` no worker.

### Slot "2ª mão"

O equipamento (inventário, `P`) tem 5 slots: cabeça, corpo, pernas, pés e **2ª mão**. Aceita um **escudo** (Shift+clique equipa) ou uma **arma de uma mão** (arrastar; ex.: uma adaga, para lutar com duas). O peso conta, a armadura não. Em **primeira pessoa**, só com um item na 2ª mão (mão direita vazia) aparecem os braços do modelo com o item na mão esquerda (antes apareciam as mãos de boxe, sem o item). O escudo fica no antebraço esquerdo; a segunda arma no osso `Left Item`. Quando a arma da 2ª mão (ou o escudo) é a que bate/bloqueia, é ela que gasta a durabilidade.

**2ª mão desabilitada**: com uma arma de **duas mãos** (`"estilo": "2h"`) ou de **haste** (`"polearm"`) na hotbar, ou com uma arma feita para se usar **só com uma mão** (`"mao_unica": 1` nos atributos dela em `item_balancing.json`; nenhuma arma vem assim de início), o item da 2ª mão não conta para nada: o estilo é só o da arma da hotbar, o escudo não bloqueia e a segunda arma não bate. **Em combate (guarda, golpe, bloqueio) ele some da mão**; relaxado continua à vista. No inventário o slot fica avermelhado e o ícone apagado. Os outros jogadores também deixam de o ver em combate.

### Armas no osso `Right Item`

O `player.bbmodel` novo tem o osso `Right Item` (filho do antebraço direito), que as animações de combate giram: as armas de uma mão (`armas` em `item_balancing.json`) ficam presas a ele e **a lâmina segue o golpe** (já não há `attack_clips`/`attack_blade` no `itens.json`). O alinhamento é automático (`build_on_bone` em `player_advanced/held_item.py`): a lâmina (o eixo mais comprido do modelo, do cabo para a ponta) aponta para -Y do osso — "para a frente do punho", como o guia assumiu — e a largura da lâmina para +Z do osso; o cabo (origem do modelo) fica na mão. Se uma arma aparecer girada, `"hand": {"roll": 90}` em `itens.json` gira-a à volta da lâmina. O `hand.scale` continua a valer. Foi criado também o `Left Item` (espelho, no antebraço esquerdo) para a segunda arma.

**A arma não atravessa o corpo** (`player_advanced/blade_avoid.py`): as animações foram feitas para uma lâmina média e, em alguns momentos (a preparação do corte de volta, a varrida de duas mãos), a lâmina passava por dentro da cabeça ou do tronco — mais ainda com armas compridas. A cada frame o modelo mede a lâmina (do punho à ponta) e as caixas do tronco, cabeça e pernas (e do outro braço, nas armas de uma mão; nas de duas mãos o braço esquerdo segura o cabo e não conta) e, se a lâmina entra numa delas, gira a arma à volta do punho para a direção livre mais perto (até 80°, com 4 cm de folga), suavizado para não tremer. A caixa de golpe segue a arma corrigida. Conferido com os clipes de combate e lâminas de 0,7 a 2,6: de 195 quadros a atravessar sobram só os 2–3 em que a própria mão está encostada à cara (o corte de volta), e aí só o pedaço junto à mão.

O escudo fica no antebraço esquerdo, com a face para a frente no `block_shield`. Os eixos do modelo do escudo dizem-se em `itens.json` → `"escudo": {"face": "-x", "cima": "z"}`.

## Pastas de modelos

```
assets/models/
├── player/player.gltf               modelo do jogador (com os clipes)
├── armaduras/armadura1/, armadura2/ cabeca, corpo, pernas e pes (.gltf)
├── armas/Sword of the Hero/         espada do heroi (.obj + .mtl + textura)
└── produced_blocks/                 baú, minoculus-grump, banco de madeira, stand
```

## Modelos OBJ

Além de glTF/GLB, o jogo carrega **Wavefront `.obj`** (`engine/obj_loader.py`, escolhido pela extensão em `engine/model_loader.py`). Vale para o item na mão (`model` ou `hand.model` em `itens.json`) e para blocos `produced` (`model` em `blocks.json`).

- O `.mtl` citado em `mtllib` e a textura `map_Kd` são procurados ao lado do `.obj`, por isso mantenha os três na mesma pasta (como em `armas/Sword of the Hero/`). Sem textura, usa a cor `Kd`.
- Triângulos, quads e polígonos; uma malha por material; texturas pixel-art com filtro *nearest* e corte de alpha, como no glTF.
- Os eixos seguem a mesma conversão do glTF exportado do Blockbench, então um modelo deve aparecer virado igual nos dois formatos. Se não, ajuste `hand.rotation` (item) ou `rotation_offset` (bloco).
- Limitações: sem animações e sem ossos. Blocos animados e armaduras continuam em glTF.

## Armas: `espada do heroi`

Primeira arma do jogo. Em `itens.json` tem `"type": "weapon"`, o modelo `assets/models/armas/Sword of the Hero/sword-of-the-forgotten-hero.obj` e o ícone `assets/icons/espada_heroi.png` (gerado a partir do próprio modelo). Armas e armaduras não empilham (`max_stack_for` em `items.py`). A conta de teste já a tem na hotbar (tecla `4`).

Os atributos ficam em `item_balancing.json`, categoria `armas`:

```json
"armas": { "espada do heroi": { "fator": 1.8, "range_add": 1, "estilo": "1h", "velocidade": 1.0, "durabilidade": 1500 } }
```

- `estilo`: `"1h"` (uma mão), `"2h"` (duas mãos) ou `"polearm"` (lança/alabarda/cajado): escolhe a guarda, o bloqueio e o combo (ver "Combate").
- `velocidade`: velocidade dos golpes (1 = os tempos do guia).
- `fator`: o dano do golpe é o soco da força x fator.

Só contam **enquanto a arma está selecionada na hotbar** (ou no slot da 2ª mão, para a segunda arma). O alcance do golpe é a **caixa de colisão** da arma (ver abaixo), esticada `range_add` blocos. O painel `F4` mostra os dois, com a origem (ex.: `1.7 blocos  (geométrico 0.7 + 1)`). O tamanho da espada na mão ajusta-se em `itens.json` → `hand.scale`, com recarga ao vivo.

## Armas novas

Oito modelos novos (`assets/models/armas/<pasta>/`, ícones em `assets/icons/`). Ganham-se pela **pedra de transmutação** (`receitas.json`) e, para testar, cada conta recebe uma de cada (duas adagas) uma vez (`itens_iniciais` no `economia.json` do servidor, "TESTES").

| Item (id) | Modelo original | Estilo | Fator | Velocidade | Alcance extra | Durab. | Peso | Almas |
|---|---|---|---|---|---|---|---|---|
| Adaga do viajante (30) | traveler-dagger-polyjo | 1h (cabe na 2ª mão) | 0,9 | 1,3x | 0,3 | 400 | 0,5 kg | 400 |
| Espada de aço (31) | cool-sword | 1h | 1,4 | 1x | 0,8 | 800 | 1,5 kg | 700 |
| Machadinha Grimhew (32) | grimhew-axe | 1h | 1,6 | 0,9x | 0,5 | 700 | 2 kg | 800 |
| Montante da Lua Negra (33) | dark-moon-greatsword | 2h | 2,6 | 1x | 1,2 | 1200 | 4 kg | 1500 |
| Ceifador Cinzento (34) | ashen-reaver | 2h | 3,0 | 0,9x | 1,2 | 1400 | 6 kg | 2000 |
| Lança de ferro (35) | iron-spear | polearm | 1,5 | 1x | 1,8 | 700 | 2,5 kg | 700 |
| Tridente (36) | weapon-starter-trident | polearm | 1,8 | 0,95x | 1,6 | 900 | 3 kg | 1000 |
| Escudo de ferro (37) | shield | escudo | 0,9 (empurrão) | | | 450 | 6 kg | 600 |

As velocidades seguem o guia (adaga 1,3x, machadinha 0,9x, martelo/machado grande 0,9x). As de duas mãos e as lanças usam as guardas, bloqueios e combos delas (`combat_idle_2h`, `block_2h`, varrida → vertical, golpe carregado; `combat_idle_polearm`, estocadas). O tamanho na mão é o `hand.scale` em `itens.json` (adaga 0,6 … lança 2,6).

**Durabilidade só gasta quando acerta**: um golpe que não toca em nada não gasta a arma. Num mob, só gasta se o servidor aceitou o golpe; e a lâmina que passa rente ao **chão onde o jogador pisa** já não conta como acertar o piso (antes os golpes largos "batiam" no chão, danificavam-no e gastavam a arma).

**Espada de aço e Machadinha Grimhew** estavam viradas para o lado errado: `"hand": {"roll": 180}` no `itens.json` gira-as à volta da lâmina.

**Tooltip das armas** (passar o mouse no inventário): só o que interessa — **Dano** (o que o golpe dá de verdade com a sua força e a sua carga de agora, já calculado), **Multiplicador de dano** (o `fator` da arma), **Velocidade de ataque** (a da arma), **Peso** e **Durabilidade**. Nos escudos, "Dano do empurrão".

**Como os modelos entraram** (`tools/arma_para_jogo.py`, novo):

```
python tools/arma_para_jogo.py assets/models/source/armas/grimhew-axe.bbmodel assets/models/armas/machadinha_grimhew/machadinha_grimhew.gltf --pega 0.14 --ponta 1,1,0 --icone assets/icons/machadinha_grimhew.png
python tools/arma_para_jogo.py assets/models/source/armas/shield.bbmodel assets/models/armas/escudo_ferro/escudo_ferro.gltf --escudo --icone assets/icons/escudo_ferro.png
```

- converte com `tools/bbmodel_to_gltf.py` (cubos, malhas e rotações; os modelos "Java" sem grupos também);
- **texturas animadas** (a da machadinha tem 6 quadros empilhados, 64x384) ficam só com o 1º quadro;
- **endireita** a arma: o eixo mais comprido (PCA da superfície) vira o eixo da lâmina, com a ponta para cima (`--ponta` = direção aproximada da ponta no modelo original; os modelos "Java" vêm desenhados na diagonal, como a machadinha); a largura fica de lado e a espessura de frente;
- põe a **pega na origem**: no meio do cabo, a `--pega` do comprimento a partir do fim do cabo (adaga 0,14, espadas 0,06–0,07, machados 0,14–0,16, lanças 0,33). É esse ponto que fica na mão;
- escudo: a face decorada vai para -X e o centro para a origem (o que o `"escudo": {"face": "-x", "cima": "z"}` espera);
- `--icone`: desenha o ícone 64x64 do inventário a partir do próprio modelo (com a textura), na diagonal.

### Caixa de colisão das armas (hitbox)

Uma arma na mão (`armas` em `item_balancing.json` + modelo 3D) tem uma **caixa de colisão com o comprimento, a largura e a espessura do próprio modelo, já multiplicados pela escala** (`hand.scale`). A caixa vai presa ao osso `Right Item` e segue todas as animações. Durante a **janela de impacto** de cada golpe (não no golpe todo), a cada frame, a caixa é testada contra as caixas dos alvos (teste de eixos separadores entre caixas orientadas); **cada alvo que ela tocar leva o dano uma vez por golpe**. Assim o alcance é geometricamente correto: o que a lâmina alcança de verdade, no ângulo em que está.

- A caixa de colisão é **só a arma** (o tamanho geométrico), para um futuro parry entre jogadores poder usar a caixa real da lâmina. É ela que aparece no `F4`.
- `range_add` (blocos) dá alcance extra ao golpe **sem mexer na caixa da arma**: é um volume à parte, da largura da lâmina, que começa na ponta e vai `range_add` blocos para a frente, e só serve para decidir se o golpe acerta (não aparece no `F4`). A espada mede ~0,7 bloco e com `range_add: 1` acerta até ~1,7 (o F4 mostra `1.7 blocos  (geométrico 0.7 + 1)`). Sem `range_add`, vale só o tamanho do modelo.
- Alvos: blocos `produced` com um evento `"trigger": "hit"` (ex.: o `stand`); a caixa deles são os colliders de cada célula.
- Com o **Inspecionar (`F4`) aberto**, a caixa da arma (só a geométrica) e as dos alvos por perto aparecem **em amarelo**, por cima dos blocos.
- **Blocos do mundo:** o golpe não é só no bloco mirado: durante a janela de impacto, **qualquer bloco que o alcance da espada pegar** (a caixa da espada + o `range_add`) reage pela física (nada, move ou quebra, conforme o dano e a resistência efetiva dele), uma vez por golpe cada.
- Socos e itens sem caixa continuam a acertar o bloco da mira dentro do `range_ataque`. Colocar e coletar blocos também usam o `range_ataque` (o `range_add` da arma não conta para isso).
- Código: `player_advanced/weapon_hitbox.py` (`WeaponHitSystem`); a caixa local do modelo é guardada em `held_item.py` (`_mark_hitbox`).

## Modelo do jogador em .bbmodel

`assets/models/source/player.bbmodel` é o modelo do combate (41 animações, ver "Combate"), com duas mudanças: o `roll` é o do jogo (o do ficheiro novo era a versão antiga, a rolar para trás) e foi criado o osso `Left Item` (filho do `Left Forearm`, espelho do `Right Item`), como o guia sugere. As animações `_f` estão lá todas.

O jogo usa `assets/models/player/player.gltf` (o export do Blockbench, com as malhas); as animações novas entram nele sem mexer no resto:

```
python tools/bbmodel_to_gltf.py assets/models/source/player.bbmodel novo.gltf
python tools/add_gltf_node.py novo.gltf assets/models/player/player.gltf "Right Item" "Left Item"
python tools/copy_gltf_animation.py novo.gltf assets/models/player/player.gltf combat_idle_1h attack_1h_right ...
```

`add_gltf_node.py` (novo) copia ossos sem malha pendurando-os no osso do mesmo nome; `copy_gltf_animation.py` copia os clipes (um clipe com o mesmo nome é substituído). Os clipes antigos `one_handed_*` continuam no glTF mas já não são usados. Conferido: as poses dos 29 clipes novos no `player.gltf` são iguais às do `.bbmodel` (o conversor amostra a 30 quadros/s, como o guia).

## Baú grande

Bloco **`bau grande`** (id 29, modelo `assets/models/produced_blocks/big-chest.gltf`, fonte em `assets/models/source/big-chest.bbmodel`, ícone `bau_grande.png`). Funciona igual ao baú de madeira:
- botão direito abre/fecha, com as animações `open` (a tampa sobe a 60° e o baú balança) e `close`, feitas a partir da animação `chest` do modelo;
- é um contentor de **32 espaços** (o máximo do ecrã de baú; o de madeira tem 24).

**Espalhados pela dungeon:** **5 por andar**, 50 nos 10 andares (`"baus": {"bloco": "bau grande", "por_andar": 5, "espaco": 8}` em `DUNGEON_ROOMS`, `server/src/db.js`). Ficam em sítios livres do labirinto (chão por baixo dos 2x2 e ar nas 2 células de cima), a pelo menos 8 blocos uns dos outros e fora da clareira do minoculus; o sorteio é fixo pela semente. O servidor põe-nos ao criar a dungeon e também numa dungeon que já existia sem eles (marca `baus_v1` no documento da sala, para não repetir). Vêm vazios.

**Animação de abrir (corrigido):** abre e fecha como o baú de madeira (botão direito: tampa sobe com um balanço; ao fechar a janela, desce). Não animava porque o nó animado `goldchest` é a **raiz** do modelo e tem malha: o Panda importa a malha como filho com o mesmo nome, e o leitor de animações (`_resolve_node_paths` em `engine/gltf_animation.py`) via dois `goldchest` com a mesma hierarquia, achava "ambíguo" e desligava a animação inteira do modelo (só um aviso na consola). Agora, nesse empate, fica com o nó menos profundo (o nó em si, não a sua malha), e serve para qualquer modelo assim.

Ocupa **2x2x2 blocos** (escala 2). Pesa 120 kg: empurra com 20 de dano e quebra com 80. A animação `chest` original (aparecer, abrir, fechar e sumir) também está no modelo, para usar depois (por exemplo, ao aparecer loot).

## Dungeon 1 (sala pública de nível 2)

As salas públicas agora têm **nível** (`"nivel"` no documento da sala; sem o campo = 1). A **Praça pública** é o nível 1. A **Dungeon 1** (`room_dungeon_1`) é o nível 2.

- **Como se entra:** pelo **minoculus de uma sala pública do nível anterior**. A dungeon só aparece no portal para quem está na praça (ou já está dentro dela). O servidor confere em `POST /api/worlds/:id/join`: tens de estar ligado (ENet) a uma sala pública de nível 1. Se não estiveres, dá 403.
- **Mobs**: 12 esqueletos por andar e **4 Sombras no 1º andar** (o de baixo, onde se chega pelo minoculus), com recarga de 40 s. Em `mobs.json` → `"spawn": {"dungeon": {"por_andar": 4, "andares": [1]}}`: `andares` (contados de baixo, 1 = o 1º) prende um tipo a certos andares; sem ele, nasce em todos.
- **Não fica na tua lista de mundos.** Não entra no `link_room_properties` nem no login: quem entra recebe um **passe em memória** (`server/src/access.js`) que vale enquanto estás lá e mais 2 min depois de sair (para recarregar / ligação a cair). Depois disso tens de voltar a passar pela praça.
- **Geração nova `"geracao": "caixa"`:** uma caixa oca de `"material"` (**pedra dura**) do tamanho da sala, **200 x 200 de largura e 102 de altura**: chão (y=0), teto (y=101) e 4 paredes de 1 bloco, sem árvores. Mesma regra no servidor (`server/src/world/worldgen.js`) e no jogo (`CRUD_room.py`).
- **Dentro da caixa** (`"revestimento"` e `"camadas"` no documento da sala):
  - as 4 paredes são **forradas por dentro de pedra** (1 bloco), com **fragmentos solares** aqui e ali (0,4%, ~1200);
  - um **piso de pedra em y=1** (por cima da pedra dura) e depois **a cada 10** (y = 11, 21, … 91): **10 andares de 10 de altura** (9 de ar; antes eram 6 de 16), também com fragmentos espalhados (0,15%) (`"camadas": {"a_cada": 10, "inicio": 1}`);
  - **escadas em caracol largas** (`"escadas": {"por_andar": [3, 4], "bloco": "pedra", "luz": "fragmento solar"}`): **3 ou 4 por andar** (31 no total), cada uma sobe do seu andar ao de cima. Ocupa 9x9 no meio de uma célula do labirinto (nunca bate em paredes): **pilar 3x3 no meio e um anel de 3 blocos de largura** à volta (antes era 1), dividido em 10 setores (`STAIR_SECTORS`, igual no jogo e no servidor). O setor 0 é a entrada e cada setor seguinte tem um degrau 1 bloco mais alto (cheio por baixo); sobe-se a saltar (o salto é de 1,2 blocos). O 9º degrau dá para o piso de cima, que tem um **buraco** por cima dos degraus altos para a cabeça passar (e por onde se pode descer). A rotação e o sentido (horário/anti-horário) são sorteados. **Iluminação:** 2 **fragmentos solares no topo do pilar**, encostados ao teto, do lado dos degraus baixos: iluminam a escada mas não ficam onde se pisa nem ao alcance de quem sobe ou salta (conferido: nenhum dos 62 pode ser tocado). Sorteio fixo pela semente; não ficam na clareira nem na mesma célula da escada do andar de baixo. Conferido com um jogador simulado (1,8 de altura, salto 1,2): **100% de cada um dos 10 andares** é alcançável a pé a partir do minoculus;
  - em cada andar, um **labirinto** de paredes de pedra do chão ao teto (`"labirinto"`): grelha de células de 14 blocos, algoritmo "árvore binária" (há sempre caminho para todo o lado; conferido: 100% do andar alcançável) mais 20% de paredes abertas a mais (`aberto_extra`, atalhos e voltas), com alguns fragmentos (0,3%). Cada andar tem um labirinto diferente; no de baixo há uma **clareira** de 8 blocos à volta do minoculus.
  - Onde vai cada fragmento sai de um hash da célula com a semente (`cellHash` no `worldgen.js` = `cell_hash` no `CRUD_room.py`): o servidor e o jogo geram exatamente o mesmo (conferido: 1 420 881 blocos, 1971 fragmentos, iguais nos dois).
  - Tudo o que foi gerado e ninguém mexeu é **fixo como o chão**: quebrar um bloco do piso suspenso só tira esse bloco (antes, os vizinhos cairiam em cascata). Blocos postos pelos jogadores caem normalmente.
- **Minoculus no meio** do andar de baixo, em cima do piso de pedra: (100, 2, 100); o jogador aparece ao lado dele.
- **Baús grandes espalhados**: 5 por andar, 50 no total (`"baus": {"bloco", "por_andar", "espaco"}` em `DUNGEON_ROOMS`). Ficam em sítios do labirinto com chão por baixo e espaço 2x2x2, a pelo menos 8 blocos uns dos outros e fora da clareira do minoculus. O sorteio é fixo pela semente (`dungeonChestSpots` em `server/src/db.js`). São postos ao criar a dungeon **e também numa dungeon que já existia** (marca `baus_v1` no documento da sala), por isso não é preciso apagá-la. Por agora vêm **vazios**.
- **Sem mobs, maçãs nem árvores** (`mobs.semSpawn`).
- **Custo:** ~638 mil blocos (eram ~1,14 milhão com 200 de altura).
- **Refazer a dungeon gravada**: `DUNGEON_ROOMS` tem `"versao": 4`. Se a dungeon no banco for de outra versão, o servidor (ao listar os mundos) apaga o que foi mexido nela (blocos, baús, mobs gravados), grava a configuração nova e volta a pôr o minoculus e os baús (`upgradeDungeon` em `server/src/db.js`). Não é preciso apagar nada à mão.
- **Travava (corrigido)**: cada coluna de chunk desenhava os 11 pisos e todos os andares do labirinto uns por cima dos outros (~9500 faces por chunk, ~770 mil à volta do jogador, todas com o shader de luz), e a luz tinha a altura inteira da sala. Agora, nas salas altas (mais de 48 de altura):
  - só se desenha uma **faixa de altura** à volta do jogador: 22 células abaixo e 32 acima, andando de 10 em 10 (`TALL_ROOM`, `Y_BAND`, `Y_BELOW`, `Y_ABOVE` em `engine/chunk_room.py`). Os pisos opacos escondem o resto. São ~1200 faces por chunk, 8x menos. Ao subir/descer 10 células os chunks refazem-se aos poucos;
  - a **região da luz** também anda na vertical (72 de altura em vez de 284), e a composição deixou o `np.maximum.at`: cada atualização passou de ~60 ms para ~12 ms, e andar com luz na mão de ~70 ms para ~20 ms.
- **Luz sem piscar**: pôr ou tirar um bloco perto de uma pedra/fragmento solar já não apaga a luz dela enquanto é recalculada. A luz antiga continua desenhada até a nova estar pronta (as desatualizadas são refeitas primeiro), e uma fonte posta agora acende na hora.
- **Luz só perto**: com ~2000 fontes de luz, o jogo calcula a luz de cada fonte **só quando ela alcança a região à volta do jogador**, e as novas aos poucos (25 ms por frame, `FLOOD_BUDGET` em `engine/lighting.py`), para não travar ao andar.
- O servidor cria a dungeon sozinho (`DUNGEON_ROOMS` / `ensurePublicRoom` em `server/src/db.js`). Só existe **online** (no modo local não).

## Desempenho no combate

O que foi otimizado para o combate não perder FPS:

- **Teste de colisão entre caixas** (`boxes_intersect` em `engine/mob_rig.py`, usado pela espada, pelos mobs e pelo modo local): reescrito com a fórmula clássica (Gottschalk/Ericson), com números simples e um teste de esferas primeiro. Passou de **~160 µs para ~3 µs** por teste, com os mesmos resultados (conferido em 20 000 pares).
- **Alvos do golpe só ao alcance**: em cada frame do swing, a espada calculava e testava as caixas de **todos** os mobs (corpo, espada, escudo), projéteis e jogadores da sala. Agora só os que estão a 4 blocos + `range_add` (`WEAPON_NEAR_BLOCKS` em `main.py`); os impactos de blocos só os mobs a 30 blocos (`IMPACT_NEAR`); o alvo mais perto em terceira pessoa também filtra pela distância. Com 12 esqueletos à volta: **~24 ms → ~0,8 ms** por frame de golpe.
- **Animação do jogador, dos outros jogadores e dos blocos animados (baús)** (`engine/gltf_animation.py`): poses pré-calculadas a 60 fps por modelo (como os mobs), com resultados iguais aos de antes. Passou de ~0,56 ms para ~0,006 ms por frame por jogador. O ficheiro glTF e as curvas também ficam em cache: cada baú grande ou jogador novo já não relê o ficheiro. Custa ~0,5 s ao abrir o primeiro modelo do jogador (no carregamento). A capa da armadura (que só anima os ossos livres) continua com o cálculo normal (`drop_bake`).
- **Interface**: as barras de vida e estamina só refazem o texto quando o número mostrado muda (antes, quase em todos os frames durante o combate, porque a estamina mexe sem parar) e só mudam de cor quando muda; os avisos ("Na cabeça!", "... quebrou!") usam sempre o mesmo texto em vez de criar um novo com fundo a cada aviso.
- **Projéteis dos mobs**: as entidades são reaproveitadas (a auréola do anjo criava e destruía 24 entidades por ataque).
- **Outros jogadores**: o estado só é reaplicado quando chega um snap novo (antes, em todos os frames).

Se ainda cair o FPS num combate, carregue **F3** (ou **M**) no meio da luta: grava 5 s e escreve `perfil_fps.txt` (nos Downloads) com o que gastou mais tempo.

## Mobs

**Onde nasce cada um** (tabela `"spawn"` de cada mob em `mobs.json`, por zona: `"publica"` = praça, nível 1; `"dungeon"` = salas públicas de nível 2+; `"privada"` = mundos dos jogadores e modo local):

- **Praça (mundo público 1): só mobs passivos.** 8 de cada um dos 5 tipos, ou seja 40 no total, espalhados pela sala toda (`"espalhar": true`).
  - Ao abrir a sala nasce a população toda de uma vez (`"encher": true`).
  - Depois só nascem quando o número baixa, um de cada vez, com **40 s de recarga** entre cada um (`"recarga"`). A recarga só começa a contar quando falta algum.
- **Dungeon: esqueletos em todos os andares.** **12 por andar** (`"por_andar": 12`), 120 nos 10 andares, num sítio livre do labirinto. Enche ao abrir; depois volta um a cada 20 s, sempre no andar com menos esqueletos. O servidor gasta ~0,9 ms por tick com os 120.
- **Anjo e sombra** já não nascem em lado nenhum (spawn vazio); os modelos e a IA continuam no jogo.
- Ao abrir a sala, os mobs gravados acima do máximo da zona somem (`trimToMax`).

**Rede dos mobs (corrigido: a praça travava com 40 mobs).** O servidor mandava o estado de todos os mobs a cada jogador 20x/s como pacote **fiável**: ~9 KB por pacote, ~175 KB/s por jogador, no mesmo canal dos blocos. Uma perda de pacote fazia tudo o que vinha atrás esperar (blocos, outros jogadores, golpes). Agora (`server/src/realtime/server.js`, `backend/remote.py`):
- **formato compacto**: cada mob é uma lista curta `[id, tipo, x, y, z, yaw, vida, clipe, tempo, extra?]` (`packed()` em `mobServer.js`);
- os mobs **até 32 blocos** vão em todos os ticks (20x/s); a **lista inteira** só 4x/s (`"full"`, e é aí que o cliente apaga os que sumiram) (`NEAR_MOBS`, `FULL_EVERY`);
- o estado vai **sem fiabilidade** no canal 1 (perder um não atrasa nada; o próximo substitui-o); os **eventos** (golpes, mortes, drops) vão à parte, fiáveis (`{t:"ev"}`);
- resultado: ~2,2 KB 4x/s + ~0,2 KB 16x/s, ou seja **~12 KB/s** (15x menos).
- **só os mobs que interessam**: cada jogador só recebe os mobs a até 80 blocos na horizontal e 24 na vertical (`SEND_MOBS`, `SEND_MOBS_Y`). Na dungeon são só os do teu andar: com 120 esqueletos, o pacote completo cai de 15,7 KB para ~1,2 KB.
- No jogo, os mobs além da névoa (122 unidades) **nem se desenham nem animam**, e um mob sem atualização nova não é reaplicado.

**Desempenho dos mobs no jogo (FPS)** (`client/mobs_view.py`):
- **Poses pré-calculadas**: ao carregar cada modelo (no ecrã de carregamento) calcula-se uma vez a matriz de cada osso em cada frame (30 fps) de cada animação, partilhada por todos os mobs desse tipo. Animar um mob passou a ser só copiar ~20 matrizes: a parte em Python ficou ~60-100x mais rápida (bloomlet 0,45 → 0,007 ms; sombra 1,97 → 0,018 ms por atualização), com a mesma pose de antes. Custa ~0,1 s por modelo ao carregar (a sombra ~2 s).
- **Versão de longe**: a mais de 22 blocos (`STATIC_DIST`) o mob troca por uma cópia parada do modelo (primeira pose do idle) fundida numa peça só: **1 chamada de desenho em vez de ~18** e sem animação. Perto volta ao modelo animado, no ponto certo da animação. Um mob com partes arrancadas ou a morrer fica sempre animado.
- Além da névoa (122 unidades) não se desenham nem animam.
- Animação por distância: até 16 blocos 30x/s, até 22 blocos 12x/s, atrás da câmara 3x/s.
- **Reaproveitar modelos**: um mob que sai de vista (longe, outro andar, morreu) não é destruído; o modelo fica guardado (até 16 por tipo) e serve o próximo mob desse tipo, sem copiar o modelo nem refazer o animador (antes, cada mob a aparecer dava um soluço).
- **Menos trabalho por frame**: posição em números simples (não numpy), o nó só se mexe quando o mob se mexe (direto no Panda, sem passar pelo Ursina), os da versão de longe só 15x/s, a cor só muda quando muda, e a barra de vida só se cria no primeiro dano (3 entidades a menos por mob).

### Esqueleto: alcance, velocidade e jeito de lutar

Testado no simulador do servidor contra um jogador parado, a andar à volta, a recuar, a fugir e a rolar para o lado.

- **Mais alcance**: a caixa da espada ficou maior (`"pad": 9`); a ponta chega a ~2,7 blocos no corte e ~2,8 na estocada (antes ~1,9-2). O `"alcance"` de cada golpe passou a bater com o alcance real (antes a estocada dizia 2,0 mas a espada chegava a 1,9; o esqueleto começava golpes que não chegavam).
- **Mais velocidade de ataque**: `"velocidade"` em cada ataque toca o clipe mais depressa (corte 1,35x, estocada 1,3x, soco 1,3x), igual no servidor (janelas do acerto em tempo do clipe) e no jogo (`rate` no snapshot); cooldowns menores (corte 0,8 s, estocada 1,1 s, soco 0,7 s).
- **Mais esperto**:
  - na preparação do golpe vira-se para o alvo e **aproxima-se se ele fugiu**; quando a janela do acerto começa, a mira fica presa (quem rola para o lado nessa hora escapa);
  - **antecipa quem foge**: o servidor mede a velocidade de cada jogador e o esqueleto só começa um golpe se, quando ele chegar, o alvo ainda estiver ao alcance;
  - **escolhe o golpe pela distância** (`"alcance_ideal"`): corte de perto, estocada (com investida) mais longe;
  - persegue sempre a correr (4 blocos/s; antes passava a andar a menos de 5 blocos e nunca alcançava quem recuava devagar: era um bug com `run_range` 0).
- Resultado no simulador (30 s): parado 100% dos golpes acertam; a andar à volta ~95%; a recuar devagar ~100%; a fugir a correr (4+ blocos/s) escapa; a rolar para o lado ao ver o golpe evita parte (os cortes de perto quase não dão tempo).
- O dano por golpe não mudou (corte 4, estocada 5), mas como acerta mais e mais depressa, um jogador sem armadura (10 de vida) cai em 2-3 golpes.

### Mobs passivos (praça)

| Mob | Vida | Idle raro |
|---|---|---|
| **Bloomlet** | 18 | bloom, spin |
| **Geodo** | 28 | sparkle |
| **Melli** | 16 | buzz |
| **Morango** | 18 | happy |
| **Nimbi** | 20 | shake, rain |

- Andam devagar à volta de casa e de vez em quando fazem a animação especial deles parados.
- **Não atacam sozinhos**: não procuram jogadores (`"passivo"` em `mobs.json`).
- **Mas reagem**: quem bate num deles fica com a raiva dele **e dos do mesmo tipo a até 6 blocos** (`"bando"`) durante **15 s** (`"raiva"`). Correm atrás e dão uma investida (animação `attack`, 3-4 de dano). Depois acalmam.
- **Maiores**: escala **1,2** (era 0,8): ~1,7-1,9 bloco de altura, mais ou menos a altura do jogador. A investida chega a 1,8 blocos (era 1,4). Dão 3 almas.
- **Explodem ao morrer** (`"morte": {"explosao": {"inchar": 0.45, "raio": 4, "dano": 12, "dano_min": 4, "cor": "#..."}}` em `mobs.json`): durante 0,45 s o mob **incha, treme e fica branco**; depois **rebenta** numa nuvem de partículas da cor dele e um anel pixelado no chão do tamanho da explosão. A explosão chega a **4 blocos**: 12 de dano encostado, a cair até 4 na borda (a 1 bloco ~10; a 2 ~8; a 3 ~6; a 4 ~4; a 5 nada). Um **escudo levantado** entre a explosão e o peito apanha-a toda. Simulado no servidor (`explode` em `mobServer.js`).
- As animações de colheita dos modelos (`picked`, `harvested`, `sheared`, `regrow`, `fill`, `empty`, `closed`) ainda não são usadas.
- Fontes em `assets/models/mobs/<nome>/<nome>.bbmodel`, convertidas com `tools/bbmodel_to_gltf.py`.

### Efeitos de dano (pixel art, branco e azul)

O efeito de **qualquer dano** é proporcional ao dano: um golpe fraco faz só umas faíscas pequenas e um golpe muito forte faz algo realmente grande (`impact_plan` em `engine/shockwave.py`). As camadas vão aparecendo conforme o dano:

| Dano | O que aparece |
|---|---|
| < 4 | só **faíscas pequenas** (3 a 5 quadradinhos) |
| 4 a 10 | mais faíscas e, nos seus golpes, o **rastro** da arma (curto, só na ponta da lâmina) |
| 10 a 25 | + uma **onda de choque** (anel pixelado) no ponto atingido |
| 25 a 40 | onda maior + um **eco** (segundo anel maior logo depois) e muitas faíscas rápidas |
| 40+ | + um **clarão** (estrela pixelada) e um **anel no chão** aos pés do alvo; tudo grande |

- **Faíscas**: quadradinhos brancos e azuis que saem **no sentido do golpe** (para onde a lâmina ou o punho ia, ou de quem bateu para quem levou), com pouca gravidade. A quantidade (3 → 60), o tamanho, a velocidade e a duração crescem com o dano.
- **Onda de choque**: raio 0,3 + 0,075 x dano (unidades do mundo; 1 bloco = 2). Dá 1,05 com 10 de dano, 2,2 com 25, 3,3 com 40 e 6,3 com 80, até 9. Anéis maiores duram um pouco mais.
- **Anel no chão** (40+): 0,08 x dano (3,2 com 40, até 12).
- **Rastro dos seus golpes** (`engine/weapon_trail.py`): o jogo grava, a cada frame do golpe, a base e a ponta da lâmina (ou o punho). Quando o golpe acerta com 4 de dano ou mais, aparece a faixa que a arma varreu, e ela segue a arma até o golpe acabar. O tamanho também vem do dano (`trail_size`):
  - golpe fraco: pouco caminho (0,06 s), só a ponta da lâmina;
  - golpe forte: até 0,25 s de caminho, da base até além da ponta, e um soco largo.

  No **corte** é um arco, na **estocada** uma faixa ao longo do caminho da ponta e no **soco** uma faixa ao longo do caminho do punho (que congela quando o punho começa a voltar). Há um rastro por golpe, do tamanho do maior dano.
- Vale para tudo: os seus golpes (mobs, partes do mob, jogadores, stands, blocos), os golpes que você leva (no peito, ou na cabeça), o escudo a bloquear (as faíscas saltam do escudo), os encontrões, os blocos atirados, a queda (faíscas para cima, aos pés) e o dano de área (que além disso mostra o anel no chão do **tamanho real da área**: explosão dos passivos, onda da sombra, pulso do anjo).

Os limiares (`RING_FROM`, `ECHO_FROM`, `BIG_FROM`, `TRAIL_FROM`) e as fórmulas estão no topo dos dois arquivos. As texturas são geradas no código, os quads e as partículas são reaproveitados, e tudo some desfazendo os pixels em xadrez.

### Escudo do jogador

O **Escudo do esqueleto** pode estar na hotbar ou no slot **2ª mão** (melhor: a espada fica na direita e dá o estilo espada + escudo). Em qualquer dos casos ele fica **no antebraço esquerdo**. **Segurar o botão direito bloqueia**: o braço esquerdo leva o escudo para a frente do peito com a animação `block_shield` (0,12 s até contar como levantado). Com o escudo na mão o botão direito não coloca blocos.
- **Só bloqueia o que bate no escudo** (levantado). O cliente manda a caixa do escudo (orientada, um pouco maior que o modelo) no estado do jogador; o servidor confere-a (perto do jogador, tamanho de escudo) e testa-a **antes do corpo**:
  - golpes dos mobs: a caixa da arma do mob toca no escudo **e** o escudo está entre o mob e o jogador (uma lâmina comprida que atravessa o corpo não conta um escudo nas costas);
  - projéteis: a primeira caixa em que o projétil entra (escudo, cabeça ou corpo);
  - explosões e ondas: o segmento do centro até ao peito do jogador passa pelo escudo;
  - outros jogadores: a arma deles bate na caixa do escudo levantado (vale para o servidor com `"part": "escudo"`);
  - blocos atirados: testados no cliente com a mesma caixa.
  O que passa por cima, por baixo, de lado ou por trás do escudo acerta normalmente (sem `combat_damage`, porque estava a defender). Com o escudo baixado não bloqueia nada.
- O escudo leva **todo o dano** do golpe: o jogador não perde vida e a armadura não gasta. **Durabilidade 250** (`item_balancing.json`, "outros"; peso 4 kg): cada golpe bloqueado gasta o dano que teria dado, aparece um número cinzento ("bloqueou") e, a 0, o escudo quebra.
- **Empurrão com escudo** (`attack_shield`): é o 3º golpe do combo espada + escudo, o 2º só com escudo, e o contra-ataque (clicar a bloquear). Dano = soco x `fator` do escudo (0,8); abre a defesa dos mobs com escudo e afasta/atordoa os outros (ver "Combate").
- Os outros jogadores veem o escudo levantado (`"escudo_up"` no estado).
- Código: `shield_box` em `player_advanced/skin.py`, `build_on_bone` (`kind="shield"`) em `held_item.py`, `_update_shield`/`_shield_block`/`_combat_hurt` em `main.py`, `playerBoxes`/`shieldBox`/`shieldCovers` e o `"empurrao"` em `mobServer.js`.

Toda a IA corre no servidor (`server/src/mobs/mobServer.js`) e é configurada no `mobs.json`; as **caixas de colisão** de cada mob saem do próprio modelo, em cada instante da animação (corpo, armas, mãos, e o ponto de onde saem os projéteis), e aparecem no `F4`.

| Mob | Como luta | Animações usadas |
|---|---|---|
| **Esqueleto viajante** | Com espada: corte e estocada (avança). Com escudo: bloqueia golpes (o escudo apanha o dano). **Sem espada**: soco com as duas mãos e anda com `walk_2`. **Sem escudo**: já não tenta defender (nem com o escudo nem com os braços). | idle, walk, charge, slash, attack_sword, attack_unarmed, walk_2, begin_block, block, block_stop, block_destroy, death |
| **Anjo** | Grande (escala 2,0). Vê o jogador a 28 blocos (perde-o a 36). Voa (não cai) e fica a 8-16 blocos, andando de lado. Feixe de luz (rápido, forte, alcance 26), leque de 5 estilhaços teleguiados (alcance 22), auréola de 12 projéteis à volta quando chegam a 8 blocos, pulso de área (raio 3,5) encostado, e cura-se (bênção) abaixo de 60% de vida. | idle, spin (parado, às vezes), walk, attack_beam, attack_shards, attack_halo, attack, bless, death / death_ascend |
| **Sombra** ("mago negro") | Lâmina na mão (golpe), esmagamento de área à frente, orbe teleguiado à distância (**dano mágico**: passa pela resistência mágica, não pela armadura), aura (metade do dano por 10 s) abaixo de 50%. Golpes fortes atordoam-na (hurt). Vira no lugar quando o alvo fica atrás, salta para alvos 1-3 blocos acima. **Voa ~2,5 blocos acima do chão** quando o alvo está a mais de 5 blocos (ou 3+ blocos acima) e ataca de lá com o orbe; quando chega perto **desce** para o corpo a corpo, aterrando com fall_landing (`voo.longe` / `voo.altura` em `mobs.json`). | idle_1/idle_2, walk/walk_2, run, attack_1, attack_2, attack_orb, shadow_aura, hurt, turn_left/right, jump, fall_landing, fly_idle, fly_forward |

- **Projéteis contra blocos**: um projétil que bate num bloco dá-lhe o seu dano como um golpe (vai para a física da sala, no servidor): o dano acumula 10 s e, com força suficiente, o bloco é **empurrado** (na direção do voo) ou **quebrado**. `"dano_blocos"` no projétil multiplica esse dano (padrão 1). Os do anjo batem forte em blocos: feixe x4 (24, quebra a maioria dos blocos em 1-2 tiros), estilhaço x3 (9), auréola x2,5 (7,5).
- **Projéteis**: o servidor simula-os (velocidade, teleguiado, colisão com blocos e com as caixas do jogador; dano na cabeça x1,5) e manda-os no `snap`; o jogo desenha-os como cubos brilhantes (cor/tamanho em `mobs.json` -> `projeteis`).
- **Rebater projéteis** (todos, também os **mágicos** como o orbe da Sombra): **a defender** (botão direito), o escudo levantado ou a arma na pose de bloqueio rebate o projétil que lhe toca (o escudo gasta 2 de durabilidade); **a golpear**, a arma rebate-o numa janela mais larga que a do dano (0,3 s antes a 0,25 s depois do impacto, tempo do clipe; `_update_projectile_parry` em `main.py`). Aparece "Rebatido!". Antes: a caixa da espada (de qualquer arma com hitbox) a passar por um projétil durante o golpe **rebate-o**. Ele volta para onde estás a olhar; se olhas mais ou menos para o mob que o atirou, vai teleguiado de volta para ele. Rebatido, voa 1,3x mais rápido, dá 1,5x o dano, acerta **mobs** (não jogadores) e continua a danificar blocos. O servidor confere (o projétil tem de estar a ≤4,5 blocos de ti). Cada rebatida gasta metade do dano da arma em durabilidade. Mensagem `{t:"deflect", proj, dir}`.
- **Arrancar espada/escudo**: já não aparece mensagem; o item só entra no inventário (só avisa se o inventário estiver cheio).
- **Morte**: o mob toca a animação de morte (o anjo escolhe entre cair e subir) e só depois some; as almas contam no golpe final.
- **Arrancar a espada e o escudo do esqueleto**: são "partes" com vida própria (espada 25, escudo 20). A sua arma a bater **em cheio** na espada dele gasta a espada (nesse golpe o corpo não leva); o escudo gasta-se a bloquear ou a ser acertado. Ao quebrar, a peça **some do esqueleto** e o item vai para o seu inventário: **Espada do esqueleto** (id 24, arma de uma mão: fator 1,15, golpes 1,15x mais rápidos, durabilidade 600, os mesmos combos da espada do herói) e **Escudo do esqueleto** (id 25; na hotbar ou no slot 2ª mão, botão direito bloqueia o que bater nele, ver "Escudo do jogador"). O esqueleto passa a lutar sem eles (e guarda isso ao gravar). **Tamanho na mão**: a espada estava com `"scale": 2.2` (107% da altura do jogador, maior do que na mão do esqueleto); agora é **1,69** e o escudo **0,75** (era 0,9), exatamente o tamanho que têm no modelo do esqueleto. A caixa de golpe da espada sai do modelo, por isso o alcance da lâmina ficou proporcional.
- Configuração por mob em `mobs.json`: `ataques` (`tipo` corpo/area/projetil/cura/aura, `clip`, `janela`/`em`, `caixas`/`de`, `dano`, `alcance` [min, max] em blocos, `cooldown`, `peso`, `requer`/`sem` partes, `vida_abaixo`), `partes`, `sem_partes` (troca de clipes), `block`, `defesa`, `atordoar`, `voo`, `salto`, `distancia`, `yaw_offset` (a sombra foi feita de frente para +Z).
- Modelos: `assets/models/mobs/<mob>/<mob>.bbmodel` -> `.gltf` com `tools/bbmodel_to_gltf.py`. O esqueleto junta `skeleton.bbmodel` + espada + escudo nos ossos `sword`/`buckler`.
- O modo offline (sem servidor) continua com a IA antiga do esqueleto.

## Desempenho dos mobs

- **Caixas de colisão**: só se calcula a pose dos ossos de que as caixas dependem (a sombra tem 81 nós, as caixas usam 11; o anjo 2) e cada mob calcula as caixas **uma vez por frame** (antes: a arma, as partes do esqueleto, os impactos e o F4 pediam-nas várias vezes). No cliente (`engine/mob_rig.py`) e no servidor (`rig.js`).
- **Animação com nível de detalhe** (`client/mobs_view.py`): até 16 blocos a pose atualiza 30x/s; até 40 blocos 12x/s; mais longe 4x/s; atrás da câmara 3x/s.
- **Carregar uma vez**: modelos, caixas e clipes de todos os mobs carregam no ecrã de carregamento; os clipes são lidos uma vez por modelo (cada mob novo só aponta os canais para a sua cópia). No servidor, os modelos carregam ao abrir a sala. Antes, cada sombra ou anjo que aparecia travava o jogo um instante.
- `tools/gltf_prune_static.py` tira canais de animação parados na pose de repouso (pouco efeito nestes modelos). A primeira versão também encurtava canais constantes, mas os tempos são partilhados entre canais do mesmo osso: isso estragava o modelo do esqueleto (o jogo recusava as animações e ele ficava parado). Corrigido e modelos reconvertidos.

## Voar (bota 2)

Com a **bota 2** vestida (`"voo": true` em `itens.json`; qualquer peça de armadura com isso dá voo) voa-se como no criativo do Minecraft:
- **Espaço duas vezes** seguidas (até 0,3 s) liga e desliga o voo;
- a voar não há gravidade: **Espaço** sobe, **Shift** desce (12 unidades/s), e anda-se 1,6x mais rápido;
- descer até ao chão (com Shift) desliga o voo; tirar as botas no ar também (e aí cai).
- Ajustes em `player.py`: `FLY_DOUBLE_TAP`, `FLY_SPEED_UP`, `FLY_SPEED_MULT`.

## Rolar

**Andar + Shift + Espaço ao mesmo tempo**: o jogador rola **para a frente** ~4 blocos na direção das teclas (animação `roll`, gasta 15 de estamina, pausa curta antes de rolar de novo). Só no chão e fora de água. No combate, rolar **cancela a recuperação dos golpes e o bloqueio** (não a preparação nem o impacto, nem os primeiros 0,4 s de uma reação ao dano). Os outros jogadores veem o rolamento. A animação do `player.bbmodel` rodava para trás (a cara do modelo está em -Z/norte e o rolamento girava para +Z): foi espelhada frente/trás (rotação X e Y e posição Z com o sinal trocado, no `player.bbmodel` também). A animação veio do `player.bbmodel` e foi copiada para o `player.gltf` com `tools/copy_gltf_animation.py` (o resto do modelo não mudou).

**Correção do `player.bbmodel`**: o conversor `tools/gltf_to_bbmodel.py` invertia o sinal das rotações X/Y (e da posição X) das animações — o Blockbench não faz isso. As 12 animações antigas do ficheiro foram corrigidas (agora aparecem no Blockbench como no jogo). (O `player.bbmodel` foi depois trocado pelo do combate, ver "Modelo do jogador em .bbmodel".)

## Tela vermelha ao trocar de mundo

O flash vermelho do dano (e a camada azul debaixo de água) ficava na interface quando se trocava de mundo a meio do flash: o mundo antigo parava de o apagar e a tela ficava vermelha. Agora essas camadas (e as malhas da água) são destruídas ao sair do mundo.

## Maçãs na sala pública

As árvores da **sala pública** já não fazem maçãs (`server/physics/worker.py`). Folhas quebradas ainda podem largar uma (20%).

## Interface: o que aparece em cada janela

- **Inventário (`P`)**: somem as barras de vida e estamina.
- **Baú**: somem as barras e o painel de status (fica só o baú e o inventário).
- **Transmutação**: somem as barras e o painel de status; os **8 slots dos materiais** ficam dentro do painel da transmutação (2 filas de 4), por cima da lista de receitas.
- **Portal do minoculus**: somem as barras e a barra de itens.
- **Inspecionar (`F4`)**: as etiquetas nos blocos já não têm fundo preto; só os números — em cima **mover** (amarelo), em baixo **quebrar** (laranja).
- `InventoryUI.open_container(..., layout={"x0", "top", "columns"})` põe os slots de um "baú" noutro sítio (sem o fundo e o título).

## Pedra de transmutação (criar itens)

Item `pedra_transmutacao` (id 23): uma **safira vermelha**. Modelo `assets/models/source/pedra_transmutacao.bbmodel` (abre no Blockbench; malha lapidada de 8 lados) → `assets/models/ferramentas/pedra_transmutacao/pedra_transmutacao.gltf`; ícone `assets/icons/pedra_transmutacao.png`.

- **Botão direito com a pedra na mão** abre o inventário e o painel da transmutação, que tem **8 slots de materiais** junto da lista de receitas: ponha lá os materiais (arrastar ou `Shift+clique`).
- À direita, a lista de **receitas** — **todas** (42), numa lista com **barra de rolar** (roda do mouse por cima da lista, clicar na barra ou arrastá-la) e um campo **Pesquisar** em cima: clique nele e escreva (sem ligar a acentos: "espa" acha as espadas); `Esc`/`Enter` sai da pesquisa. Enquanto escreve, as teclas não fazem mais nada no inventário. Escolha uma: aparece o que ela pede — materiais (contados nos slots) e **almas** (do seu total) — a verde o que já há, a vermelho o que falta. **Transmutar** gasta tudo e o item vai para o inventário (se não couber, fica nos slots).
- Ao fechar, o que sobrou nos slots volta ao inventário.
- Gastar almas pode tirar pontos de status, como qualquer perda de almas.
- **A pedra gasta-se**: tem **300 de durabilidade** (`item_balancing.json`, "outros") e cada transmutação gasta o **desgaste** da receita. A **barra de uso** aparece no slot e a durabilidade aparece ao passar o mouse. O painel mostra o desgaste da receita escolhida e quanto a pedra ainda tem; em vermelho, *"a pedra vai QUEBRAR"*, quando esta é a última. A última transmutação ainda sai; depois a pedra **quebra** (some), a janela fecha e os materiais que sobraram voltam ao inventário.
- Receitas em **`receitas.json`** (relido quando muda): `"item": {"materiais": {"id": n}, "almas": n, "quantidade": n, "desgaste": n}`. Sem `"desgaste"`, vale 1 a cada 10 almas, mais meio por material, no mínimo 1 (`default_wear` em `transmutation.py`).

| Item | Materiais | Almas | Desgaste | Vezes por pedra |
|---|---|---|---|---|
| Baú de madeira | 4 madeira (`wood`) | 20 | 4 | 75 |
| Pedra solar | — | 40 | 4 | 75 |
| Fragmento solar | 1 pedra | 10 | 2 | 150 |
| Pedra x4 | — | 2 | 1 | 300 |
| Elmo / peitoral / calça / bota 1 | — | 150 / 300 / 200 / 120 | 15 / 30 / 20 / 12 | 20 / 10 / 15 / 25 |
| Elmo / peitoral / calça / bota 2 | — | 400 / 800 / 550 / 320 | 40 / 80 / 55 / 32 | 7 / 3 / 5 / 9 |
| Espada do herói | — | 1000 | 100 | 3 |

**Todos os itens e blocos se fazem aqui** (`receitas.json`; 42 receitas, uma por item). Não têm receita só as variantes que outro item já põe: `eixo deitado` e `manivela de lado` (o eixo e a manivela postos de lado) e `maca_pendurada` (a maçã a crescer na árvore). Receitas novas:

| Item | Materiais | Almas | Quantidade | Desgaste |
|---|---|---|---|---|
| Madeira (`wood`) | — | 4 | 1 | 1 |
| Terra / folhas | — | 1 | 4 | 1 |
| Pedra dura | 4 pedra | 20 | 1 | 3 |
| Maçã | 2 folhas | 3 | 1 | 1 |
| Balde | 2 pedra | 10 | 1 | 2 |
| Cola | 2 folhas + 1 maçã | 8 | 4 | 1 |
| Banco de madeira | 3 madeira | 15 | 1 | 2 |
| Baú grande | 8 madeira | 60 | 1 | 6 |
| Stand | 4 madeira | 30 | 1 | 3 |
| Minoculus | 4 pedra | 500 | 1 | 50 |
| Pedra de transmutação | 2 pedra + 1 pedra solar | 300 | 1 | 30 |
| Espada / escudo do esqueleto | — | 350 / 300 | 1 | 35 / 30 |
| **Isqueiro** | 1 pedra + 1 fragmento solar | 25 | 1 | 3 |
| **Grama** (bloco de relva; item novo, id 43) | 1 terra + 1 folhas | 2 | 1 | 1 |
| **Balde de água** | 1 balde | 2 | 1 | 1 |

A **grama** agora é item: põe o bloco de relva; partida dá terra (como antes). O servidor cobra o item do próprio bloco quando ele existe (grama) e o do `drop` só nas variantes sem item (eixo deitado → eixo).

(As armas, armaduras e as peças do parafuso/torque já tinham receita.)

A pedra vem nos itens iniciais e **compra-se no minoculus** (250 almas; ver *Portal*).

## Durabilidade e espaço dos itens

Cada unidade de arma, armadura, baú e balde tem um **status** que aparece ao **passar o mouse** no inventário (`P`), na linha de baixo da caixinha (`item_durability.py`). Os itens com durabilidade (armas, armaduras, escudo, pedra de transmutação) também mostram uma **barra de uso** no slot, na hotbar e no inventário: verde quando novos, amarela a meio e vermelha no fim. Ela é atualizada a cada golpe.

| Item | Status | Onde fica |
|---|---|---|
| armas e armaduras | **Durabilidade** `atual/máx` | máx: `"durabilidade"` em `item_balancing.json`; atual: `data["durabilidade"]` da pilha (sem a chave = nova) |
| baú (no inventário) | **Ocupado** `n/24 slots` | `data["ocupados"]`, atualizado ao pegar o baú (Q) e ao fechar o baú na mão |
| balde | **Água** `%` (níveis/4: o balde leva meio bloco) | `data["agua"]` |

Durabilidade = **quanto dano o item aguenta**:

- **Armadura**: todo dano que o jogador leva gasta as peças vestidas, repartido conforme a resistência de cada uma (o peitoral, mais resistente, gasta mais).
- **Arma**: gasta **o dano que dá** (mobs, jogadores, blocos e o stand acertados pela caixa da arma).
- **Arma contra arma**: a caixa da espada também é um alvo. Se a espada de outro jogador acerta **em cheio a sua espada**, a sua espada gasta esse dano e o golpe fica **aparado** (o corpo não leva nesse golpe). Quem bateu também gasta a dele. O servidor aceita `part: "arma"` no `pvp`; se a vítima já não tiver arma na mão, leva no corpo.
- A **0** o item **quebra** e some (aviso na tela).
- Valores (`item_balancing.json`): espada do herói 1500; elmo/peitoral/calça/bota 1: 60/120/80/50; 2: 100/200/130/80.
- A armadura vestida agora grava os dados (durabilidade) no jogador.

Itens iniciais: minoculus, stand, baú, **4 pedras solares**, **1 balde de água**, a pedra de transmutação e um isqueiro (contas que já existem recebem só o que ainda não tinham recebido).

## Água (estilo Flowing Fluids)

### Capim (e mobs que comem e bebem)

**Capim** (bloco `capim`): a forma do **primeiro capim** (savana, `assets/models/fontes/savannah-grass.bbmodel`, folhas em planos — leve de desenhar), com a **textura repintada nos verdes da grama** do jogo — **`tools/gerar_capim.py`** (converte com `tools/bbmodel_para_gltf.py` e repinta pelo brilho de cada pixel). Gera também o ícone.

- **Nasce sozinho em cima da grama** (`engine/tufts.py`): a cada 3 s tenta sítios ao acaso; onde o bloco mais alto é **grama** com a célula de cima vazia e sem água, nasce capim (50%). Máximo de 1 capim por 50 blocos de chão (sala pública 200x200: 800). Online quem o faz crescer é o servidor (worker); sozinho, o jogo. Quando é comido ou partido, volta a crescer aos poucos.
- Não é sólido (anda-se por ele), parte-se com qualquer golpe, não dá item (`collectable: false`) e **arde** depressa (capim seco: 6 s, pega 50%).
- **Fome e sede dos bichos pacíficos** (bloomlet, geodo, melli, morango, nimbi — os que têm `passivo` no `mobs.json`; em qualquer sala, menos os que voam) — `server/src/mobs/mobServer.js`, `needs`: são **dois cronómetros**. A cada **10 s** o bicho tem de **comer**: procura o capim mais perto (até 14 blocos), vai até lá, faz a animação **"comendo"** (~2 s) e o **capim some do mundo** (o Node manda `{op: "graze"}` ao worker). A cada **3 min** tem de **beber**: vai à água mais perto, faz a animação **"bebendo"** e essa **célula de água desce 1 nível (1/8)** — o servidor (`drinkWater` em `realtime/server.js`) tira o nível de `room.water`, grava no MongoDB, manda `{t:"water"}` a todos os jogadores e ao worker. Água com nível 1 bebida desaparece. Sem capim/água por perto o cronómetro fica vencido e ele volta a procurar a cada 2,5 s; se não chegar em 15 s desiste por uns segundos.
- **Animações "comendo" e "bebendo"**: acrescentadas aos 5 modelos por **`tools/mob_anim_comer_beber.py`** (mexe no nó `body` e nas pernas da frente: comendo = cabeça baixa com 4 bicadas a cada 2 s; bebendo = cabeça mais baixa e esticada, lambidas lentas). Mapeadas em `mobs.json` (`animations.comendo` / `animations.bebendo`).

### Noite: flores lunares e escuro mais fundo

- **Flor lunar** (bloco `flor lunar`, modelo `produced_blocks/flor_lunar.gltf` gerado por **`tools/gerar_flor_lunar.py`**: caules verde-escuros e botões azul-pálido, pixel art 16x16, mesmos planos cruzados do capim). Emite **luz 16** e tem `"brilha": true` (desenhada sempre clara, vê-se de longe no escuro) (`"luz"` em `block_properties`): ilumina fraco ~10 blocos à volta — dá um ambiente de noite com manchas de luz, não um breu.
- **Espalhada pela sala pública**: nasce sozinha na grama (`engine/tufts.py`, `glow_flowers`, no worker), ~1 por 110 blocos de chão (200x200 → ~360). Ao abrir a sala, enche de uma vez; se forem partidas, voltam a nascer aos poucos. Não é sólida, não dá item, os bichos não a comem.
- **Partes sem luz mais escuras**: `AMBIENT` em `engine/lighting.py` é **0,13** (era 0,16; esteve em 0,07, escuro demais). O céu não mudou.

### Lagos

A sala pública tem **lagos**: elipses de 4 a 9 blocos de raio **escavadas no chão** (2 de fundo no meio, 1 na borda) e **cheias de água**. Configuração na sala: `"lagos": {"quantidade": 7, "raio": [4, 9]}` (`PUBLIC_ROOM` em `server/src/db.js` e `backend/local.py`).

- **Onde**: sorteados pela semente com o mesmo `cell_hash` no jogo (`lakes_of` / `lake_cells` em `CRUD_room.py`) e no servidor (`lakesOf` / `lakeCells` em `server/src/world/worldgen.js`), em contas inteiras, por isso os dois escavam exatamente o mesmo (testado: os 7 lagos, as 955 células e as 391 árvores iguais). Nunca na praça (raio da praça + raio do lago + 4) nem colados uns aos outros; as **árvores** não nascem dentro nem a menos de 3 blocos da borda.
- **Água**: o buraco sai da semente; a água é posta **uma vez** — online pelo servidor ao abrir a sala (`fillLakes` em `server/src/rooms.js`, marca `lagos_agua` na sala), sozinho pelo jogo (`_fill_local_lakes`). Só nas células vazias (um bloco de jogador ali fica). Depois é água normal: corre, o balde tira, grava-se.
- A sala pública que já existia ganha os lagos ao arrancar o servidor (`ensurePublicRoom` põe `lagos` e `lagos_agua: false`); as árvores que estavam onde agora há lago somem (as que um jogador mexeu ficam).

Água **finita**, inspirada no mod *Flowing Fluids* do Minecraft (`engine/water.py`):

- Cada célula tem de **1 a 8 níveis** (8 = bloco cheio = 2 baldes). Não há fontes infinitas: a água só muda de lugar.
- A cada **0,24 s** (`TICK` em `engine/water.py`; antes 0,1 s, escorria rápido demais) ela **cai** (enche o que está por baixo), **escorre por bordas** (desce degraus), **nivela** (a superfície fica plana; procura pela mesma água, até 8 casas, um sítio 2 níveis mais baixo) e **poças de 1 nível andam** até uma descida a até 4 casas. Um balde num chão plano vira uma camada fina de 8 células.
- Um **bloco** posto em cima da água empurra-a (para cima e para os lados), menos os blocos **permeáveis** (`"permeavel": true` em `block_properties`, ex.: o parafuso de Arquimedes): a água entra neles e aparece lá dentro; tirar um bloco acorda a água à volta. Blocos caem através da água. Água que chega ao **void** some.
- Desenho (`engine/water_render.py`): uma malha transparente por chunk, altura pelo nível, só as faces que se veem, textura `assets/textures/agua.png` a deslizar. Com os olhos debaixo de água o ecrã fica azulado.
- **Nadar**: com água pela cintura anda-se a 55%; mais funda, afunda-se devagar e **Espaço** nada para cima. Cair em água com 1 unidade ou mais de profundidade não dá dano de queda. Constantes `WATER_*` em `player.py`.

### Balde

Itens `balde` (id 21, acumula 16) e `balde_agua` (id 22, 1 por slot), com `"max_stack"` em `itens.json` (novo: qualquer item pode ter o seu).

- O balde leva no máximo **meio bloco de água: 4 níveis** (`"capacidade"` em `itens.json`, `balde.capacidade`; uma célula cheia tem 8). Guarda de 0 a 4 níveis (`data["agua"]`); a **%** aparece ao passar o mouse no inventário. Sem a chave = cheio.
- **Só apanha água funda**: o balde só tira **o que passa de 3 níveis** numa célula (`"minimo": 4` em `itens.json`; tira `nível − 3`, até ao espaço que tem): de uma célula com 8 tira 4 (cheio), com 6 tira 3, com **4 tira só 1**, com **3 ou menos nada** (*"Água rasa demais"*). Os 3 níveis de baixo ficam sempre. Mirando a superfície rasa de um lago fundo, tira da célula de baixo (`WaterSim.scoop`).
- **Botão direito mirando água funda** (com espaço no balde): tira só dessa célula, pela regra acima.
- **Botão direito num bloco** (ou na água, com o balde cheio): despeja **tudo** o que o balde leva; se não couber tudo, o resto fica no balde.
- **Balde também é bloco**: **Shift + botão direito** põe o balde no mundo (blocos `balde` e `balde_agua`; sólidos: dá para subir neles). Só cheio ou vazio (o bloco não guarda a quantidade). Pegar com `Q` devolve o balde (o cheio volta a 100%).
- **Chuva no balde**: um **balde vazio posto no mundo enche com a água que cai em cima dele** (de um parafuso, de uma bica, de outro balde despejado por cima...). A água que cai entra no balde até completar os 4 níveis; aí o bloco vira **balde de água** (`"recipiente": {"capacidade": 4, "cheio": "balde_agua"}` no `blocks.json`; `WaterSim.container_of` / `on_container_full`). Online quem simula a água pede ao servidor (`{t:"bucket_fill", cell}`) e ele troca o bloco (no livro balde e balde de água são o mesmo balde). O que entrou antes de encher fica só em memória.
- **Maior**: o modelo do balde está **1,2x** maior no mundo (`scale` em `blocks.json`, ~0,9 bloco de largura) e **0,8** na mão (antes 0,55).
- Modelos: `assets/models/source/balde.bbmodel` e `balde_agua.bbmodel` (abrem no Blockbench) → `assets/models/ferramentas/balde/*.gltf`; ícones em `assets/icons/balde*.png`.
- Itens iniciais: **1 balde cheio de água**. **`F8` (teste)** dá outro. Faz-se na pedra de transmutação (2 pedra + 10 almas; cheio: balde + 2 almas).

### Fogo e isqueiro

O fogo é uma **combustão**: não é um bloco à parte, é um **estado dentro de um bloco** que arde (como a cola fica num bloco). Lógica em `engine/fire.py` (a mesma no servidor e no jogo sozinho), desenho em `engine/fire_view.py`.

- **O que arde**: blocos com `"combustivel"` no `blocks.json`: `{"segundos": quanto tempo até ser consumido, "pega": chance por segundo de pegar fogo das chamas ao lado, "altura": chamas até N blocos (máx. 3)}`. Agora: tronco (`wood`, 60 s, chamas até 3 blocos), folhas (8 s, pegam depressa), baús, banco, stand e as peças de madeira do parafuso/torque. Pedra, terra, relva, pedra solar, minoculus e baldes não ardem.
- **Isqueiro** (item 42, `isqueiro`; receita: 1 pedra + 1 fragmento solar + 25 almas; **30 usos**: cada vez que acende gasta 1 de durabilidade — a barrinha de uso aparece no slot — e a 0 acaba, *"O isqueiro acabou!"*; `durabilidade` em `item_balancing.json`, o servidor gasta o mesmo no livro): **botão direito num bloco** que arde acende-o; num bloco que **não** arde acende o **fogo no ar** em cima dele (ou do lado mirado, se houver chão por baixo) (*"Está molhado"* com água ao lado).
- **Fogo no ar**: o fogo também arde **em cima de qualquer bloco** (pedra, terra...), "queimando o ar" de uma célula vazia com um bloco por baixo. Dura pouco (**2,5 a 6 s**, `AIR_MIN`/`AIR_MAX`), não consome nada, queima quem passa e acende os blocos que ardem ao lado. Os blocos a arder lançam fogo no ar à volta (no chão ao lado, um degrau acima ou abaixo; `AIR_SPAWN`), por isso o fogo "anda" pelo chão até à madeira seguinte. Fogo no ar não cria mais fogo no ar (não alastra sem fim num campo de pedra) e há no máximo 160 ao mesmo tempo. Pôr um bloco na célula, tirar o de baixo ou água apagam-no. Online o jogo pede `{t:"ignite", cell}`; o servidor confere a distância (8 blocos) e se o jogador tem um isqueiro no livro.
- **Dano no bloco**: enquanto arde, o bloco leva dano aos poucos (no mesmo dano acumulado dos golpes, `BlockPhysics.burn`): a meio do fogo já tem **~75%** do que o parte e chega a 98% (`BURN_DAMAGE` 1,5). Um bloco a arder parte-se cada vez mais fácil (um soco acaba-o). Online quem soma é o servidor.
- **Blocos a mexer levam o fogo**: um bloco a arder que cai, tomba ou é empurrado **continua a arder** no destino (antes o fogo sumia: a física tira o bloco do mundo enquanto voa). Durante o voo o fogo espera (não arde nem espalha) até o bloco assentar, até 12 s (`FLIGHT_WAIT`).
- **As chamas** crescem de ~0,6 bloco até à altura máxima no primeiro quarto do tempo, mantêm-se e **diminuem à medida que o bloco é consumido**; no fim o bloco **some** (sem item; um baú perde o que tinha) e o que estava em cima pode cair. Sobem pelas células de cima até **3 blocos de altura**.
- **Espalha-se (mais)**: a cada segundo, cada fogo pode acender os blocos inflamáveis à volta do bloco e das chamas, agora também **em diagonal** (metade da chance), com chance `pega` x **2** (`SPREAD_BOOST`) x força das chamas. Uma árvore arde de baixo para cima e um monte de folhas 6x6 pega todo em menos de 10 s.
- **Água apaga**: água no bloco, ao lado dele ou nas chamas apaga o fogo (o bloco fica, com o que ainda não ardeu). Um balde despejado em cima resolve.
- **Queima (mais dano)**: estar nas chamas tira **2,5 de vida a cada 0,5 s** (*"Está a arder!"*) e o jogador **continua a arder 3 s** depois de sair (1 a cada 0,5 s; *"A arder! (procure água)"*); entrar na água apaga. **Mobs** nas chamas também queimam (2,5 a cada 0,5 s, no servidor, `MobServer.burnMobs`, com o número de dano); morrer queimado não dá drops nem almas.
- **Luz**: o fogo **ilumina** à volta (luz quente do sistema de luz, como a pedra solar): um ponto de luz por grupo de fogos de 3x3x3, de nível 10-18 conforme o tamanho das chamas (fogo no ar: 7), os 12 grupos mais perto do jogador (`FireView.light_sources`, `_fire_lights` no `main.py`; no máximo 2 luzes novas a cada 0,4 s, para não travar).
- **Desenho** (`engine/fire_view.py`, `engine/fire_tex.py`):
  - **Leve**: a malha das chamas só se refaz quando um fogo acende/apaga ou a altura muda meio bloco, no máximo a cada 0,6 s quando um fogo acende/apaga e a cada 1,5 s quando só muda a altura (antes refazia-se a cada 0,1 de altura de qualquer fogo, várias vezes por segundo: era isto que travava); só os fogos a até 64 blocos da câmera; no máximo 30 faíscas. A luz do fogo é um ponto fixo por grupo de 4x4x4 blocos com 3 níveis (8/10/12), no máximo 6, e muda no máximo uma por segundo (cada luz nova é um cálculo de luz). A malha da fumaça refaz-se no máximo a cada 0,5 s.
  - **Chamas vivas**: a textura é uma **simulação de chamas em tempo real** (numpy, 18 passos/s), não um ciclo de quadros: línguas que nascem, ondulam com o vento e somem, sem se repetir. Pixel art de 16 px por bloco; entre as línguas a base é fria, por isso **vê-se o bloco** a arder por trás das chamas. Há três texturas (chamas de 1,5, 2,5 e 4 blocos) para não esticar os pixels.
  - **Um fogo só**: os blocos a arder juntam-se. Cada face de fora do conjunto tem uma folha de chamas (a face entre dois blocos a arder não se desenha); blocos **empilhados** a arder com a mesma face de fora levam **uma folha só**, de baixo até às chamas do de cima (um tronco é uma coluna de fogo, sem riscas em cada bloco); os blocos de cima têm dois planos pelo meio alinhados com os eixos. As UVs vêm das coordenadas do mundo (a textura repete-se de 4 em 4 blocos sem emenda), por isso blocos vizinhos formam uma parede de fogo contínua.
  - **Faíscas e fumo**: os fogos de cima soltam faíscas (quadradinhos amarelos/laranja que sobem a ondular e somem) e fumo cinzento semitransparente que sobe e se espalha (só blocos).
  - Transparência binária nas chamas (pixel nítido, sem problemas de ordem com a água).
- **Online**: o fogo arde **no servidor** (worker Python, `RoomSim.fire`); o estado vai no campo **`fogo`** da sala (`{"x,y,z": [segundos a arder, duração]}`, com um `1` a mais no fogo no ar), gravado no MongoDB como os outros campos do mundo e mandado a todos quando muda (as mudanças juntam-se: no máximo um estado a cada 0,5 s) e a cada 3 s; o Node passa-o também aos mobs; os jogos só desenham (e avançam as chamas entre dois estados). Para a água apagar o fogo, o Node passa ao worker a água da sala (`{op:"water"}`, ao abrir e a cada mudança). **Sozinho** quem simula é o jogo, e o fogo fica no `rooms.json` (campo `fogo`). Um fogo a meio continua depois de sair e voltar.
- Modelo do isqueiro: `tools/torque_modelos.py` gera `assets/models/ferramentas/isqueiro/isqueiro.gltf` e o ícone `assets/icons/isqueiro.png` (corpo vermelho, chaminé e roda de aço).

### Oxigénio

Uma **barra azul de oxigénio**, por cima da barra de vida, **escondida** normalmente: aparece quando falta ar e some ~1,5 s depois de encher.

- **Ligada à vida**: o máximo é **2 x a vida máxima** (vida 10 → 20; cada ponto de vida +2). `Player.OXYGEN_PER_HP`.
- **Gasta**: com os olhos **debaixo de água** 1 por segundo (~20 s com a vida base); com **fumaça** nos olhos 0,3 por segundo por nível de fumaça. As duas somam.
- **Enche** 6 por segundo a respirar ar limpo.
- **A zero**: afoga (1 de vida por segundo, *"Sem ar! Suba à superfície"*) ou sufoca na fumaça (0,5 por segundo). Pisca quando está abaixo de 25%.
- Código: `_update_oxygen` no `main.py`, a barra em `player_advanced/status_bars.py`.

### Fumaça

A fumaça é um "bloco" de gás, simulado **como a água, mas ao contrário** (`engine/smoke.py`; desenho em `engine/smoke_view.py`). Cada célula de ar tem densidade 1 a 8.

- **Sai do fogo, pouca**: cada bloco a arder solta fumaça por cima de vez em quando (metade das vezes, a cada 1 s, densidade 1 ou 2 com chamas altas); o fogo no ar não solta. Dissipa-se depressa ao ar livre e no máximo há 500 células de fumaça. (Antes saía muito mais.)
- **Sobe** (~2,4 casas/s), **abre-se para os lados** ao subir e **dissipa-se** no ar (some mais depressa quanto mais alto). Quando não pode subir (debaixo de um teto, de uma copa) **espalha-se para os lados** e quase não se dissipa: uma sala fechada **enche-se de fumaça de cima para baixo**. Blocos e água não deixam passar; um bloco posto na fumaça empurra-a.
- **Desenho**: cubos cinzentos semitransparentes (mais opacos quanto mais densos), só as faces de fora da nuvem.
- **Faz mal ao jogador**:
  - com a fumaça **nos olhos** a **visão embaça**: um véu cinzento e a névoa do mundo chega perto (com fumaça densa vê-se só uns blocos);
  - respirar fumaça sobe a **exposição**; depois de ~1 s a **estamina** recupera a 30% e fica com um **teto** que desce com o tempo (até 40% do máximo);
  - gasta **oxigénio** (ver *Oxigénio*); sem oxigénio leva **dano leve contínuo** (0,5 por segundo, *"Fumaça! Está a sufocar"*). Fora da fumaça a exposição desce e tudo volta ao normal.
  - Números em `SMOKE_*` no `main.py` e no topo de `engine/smoke.py`.
- **Online**: a fumaça corre **em cada jogo** (não passa pelo servidor nem é gravada): sai do fogo, que é o mesmo para todos, e dura poucos segundos, por isso todos veem quase a mesma. Só os fogos a até 48 blocos de cada jogador soltam fumaça para ele.
- As nuvens de fumo das partículas do fogo saíram: a fumaça agora é esta.

### Parafuso de Arquimedes (levar água para cima)

Blocos `parafuso de arquimedes` (item 38) e `encaixe do parafuso` (item 41); lógica em `engine/screw.py`, desenho em `engine/screw_view.py`. Quem o faz girar é o **torque** (secção seguinte).

- **Montar**: os parafusos vão **em escada**: cada um uma célula para trás e uma para cima do anterior. A **frente** (a ponta de baixo, por onde a água entra) fica voltada para quem o põe. Encostado a uma cadeia, um parafuso ou encaixe acerta a orientação sozinho para encaixar.
- **Construção inclinada assistida**: com um parafuso (ou o encaixe) na mão, **clique com o botão direito num parafuso** já posto: o novo vai direto para o **degrau seguinte do topo da cadeia** (para trás e para cima), com a orientação dela, seja qual for a face clicada. **Shift + clique** põe um parafuso **por baixo** (no degrau antes do de baixo). Se o degrau estiver ocupado aparece um aviso; uma cadeia que já tem encaixe no topo só cresce por baixo.
- **Peso / gravidade**: parafusos presos uns aos outros **não precisam de apoio** cada um: o peso da cadeia toda (parafusos 25 + encaixe 30) vai para o **parafuso de baixo**, que precisa de um bloco por baixo. Se a cadeia pesar mais que a `carga_max` dele (**250**: o de baixo aguenta mais 9 parafusos e o encaixe), **cai tudo** o que não tem apoio próprio. Parafusos com um bloco por baixo apoiam-se como qualquer bloco. (`engine/torque.py`: `screw_chain`, `chain_load`, `held`; o servidor usa o mesmo código.)
- **Encaixe**: no degrau a seguir ao último parafuso (para trás e para cima). É a caixa de engrenagens: tem **pontas de eixo** (os encaixes de torque) nos dois **lados** e **em cima**, e a **bica** na face de trás.
- **Por onde a água sai** (antes não tinha saída): pela **bica do encaixe**, para a **célula atrás do encaixe**, à altura dele (um bloco acima do último parafuso). Ali começa o seu canal ou tanque: ponha chão debaixo dessa célula e paredes à volta. Se a saída estiver tapada ou cheia, a água espera nas bolsas. Enquanto sai água, cai da ponta da bica um **fio de água** com a textura da água a correr (dois planos cruzados, como as plantas).
- **Entrada**: o parafuso é **permeável**: a água **entra no bloco** em vez de ser empurrada, por isso pode pôr a ponta de baixo **dentro** do lago. As bolsas enchem com a água do próprio parafuso de baixo, ou da célula à frente da ponta de baixo (ou a de baixo dessa, ou a de baixo do primeiro parafuso). Água que entre num parafuso do meio vai na bolsa dele.
- **Água**: cada parafuso leva 2 bolsas (uma por volta da hélice) de `agua_por_bolsa` níveis (1). A cada volta do parafuso: a bolsa de cima despeja na saída, as outras sobem uma posição e a de baixo enche na entrada. **Ao contrário, a água desce** (a de baixo despeja na entrada e a de cima enche com a água da saída).
  - Com uma manivela (32 RPM) e a relação do encaixe (2), o parafuso dá ~1,07 voltas/s: um bloco de água (8 níveis = 2 baldes) em ~8 s, depois de a água atravessar a cadeia (2 voltas por parafuso).
  - A água é a mesma da simulação (finita). Tirar um parafuso despeja a água que ele levava.
  - Online, quem simula a água simula as bolsas e manda o estado (`{t:"screw"}`, `screw_state` a quem entra).
- **Água a descer (o parafuso como roda d'água)**: sem ninguém a girar a manivela, a água **desce** pelo parafuso quando **há muita água na saída** (nível >= 4 de 8, `desce_com` em `block_properties.parafuso`) ou quando **a saída está tapada/cheia** e ainda há água nas bolsas. A descida:
  - **gira o parafuso para trás** (16 RPM no encaixe, ~0,53 voltas/s): a bolsa de cima enche com a água da saída e a de baixo despeja à frente da ponta de baixo;
  - faz do encaixe uma **fonte de torque**: a rede ligada gira com ela (eixos e **manivelas giram sozinhos**, no sentido contrário ao de subir), com 48 SU por parafuso da cadeia;
  - depois de começar, **continua até as bolsas ficarem vazias** (a água que já ia na hélice chega toda lá abaixo) e pára;
  - uma **manivela a ser girada manda mais** do que a água (dá para continuar a subir água mesmo com a saída cheia);
  - uma cadeia **sem encaixe** também gira sozinha com água no topo;
  - aparece o aviso *"A água está a descer pelo parafuso…"* quando começa perto de si. (`ScrewSystem.water_drive`, `TorqueSystem._spin` com `fonte = "agua"`.)
- **Modelos** (`tools/torque_modelos.py` gera os `.gltf`, os `.bbmodel` e os ícones de todas as peças):
  - **Estilo do jogo** (pixel art 16x16, cubos): calha com arcos de ferro e, dentro, o rotor como num parafuso de Arquimedes de verdade: um **eixo redondo** fino (tronco de 8 lados) e uma **hélice contínua de chapa de aço** (uma fita em espiral do eixo até à borda, com espessura; 12 segmentos por volta, lisa, sem degraus); o encaixe é uma caixa de engrenagens com moldura escura, placa de ferro com o furo do eixo, pontas de eixo nos lados e em cima, e a bica atrás.
  - **Texturas a partir das do jogo** (não das tábuas do Minecraft, que não existem no jogo): toda a madeira (calha, bica, caixa do encaixe, braço e punho da manivela, eixos) sai do bloco **wood**: `wood_sides.png` (com o veio ao comprido de cada peça; um pouco mais clara na calha, mais escura no braço, com moldura na caixa) e `wood_top_bot.png` (os anéis) nas pontas dos eixos e nos cortes. O ferro e a pá são **metal liso** (só um chanfro de luz/sombra; a pá com um degradê do eixo para a borda), sem pintas nem rebites, que davam granulado. As bolsas usam a própria `agua.png`.
  - **Sem linha entre blocos**: as tampas dos cortes pela célula estão em peças à parte (`Carcaca_tampa_baixo/_cima`, `Rotor_tampa_baixo/_cima`) que só aparecem **sem vizinho** desse lado; com vizinho a calha e o rotor continuam no bloco seguinte e a tampa ficava lá dentro com a aresta encostada à superfície de fora, a fazer uma linha. Nas juntas entre blocos (e dentro da caixa do encaixe) não há tampas.
  - **Na mão do jogador** (e num bloco a cair, e no 1.º frame no mundo) o modelo é o de um bloco sozinho, sem água: as peças da junta e as bolsas são escondidas ao carregar o glTF (`ChunkRoom.hide_world_only_parts`; na mão saem também da caixa usada para o encaixe). Antes aparecia a água das bolsas, opaca.
  - **Sólidos fechados** (o jogo só desenha a frente das faces: *backface culling*): cada caixa, cada segmento da hélice, o eixo e as bolsas de água são sólidos fechados com as faces viradas para fora (orientadas pelo centro do sólido ao serem criadas). Os cortes pela célula (`split_mesh`) fecham cada sólido cortado com uma **tampa** do material dele (o eixo com os anéis da madeira, a calha com tábua, a pá com ferro). Antes as pontas cortadas eram ocas (via-se a relva através da calha) e a pá, chapa sem volume, ficava transparente vista de alguns lados. Verificado: todas as arestas de todos os sólidos fecham (cada uma em duas faces, em sentidos opostos), e renders com culling e fundo magenta de vários lados não mostram buracos.
    - A borda de dentro da pá fica deitada nas faces do eixo (os segmentos partem-se também nos cantos do eixo de 8 lados): não entra no eixo, e as tampas de um e de outro não lutam.
    - O glTF tem `doubleSided: false` (como o jogo já desenhava).
  - **Sem z-fighting** (faces no mesmo plano, que piscam): verificado com os blocos montados (cadeia de parafusos + encaixe + eixos + manivelas + terra à volta) e em várias fases da rotação, com tolerância de 0,25 px.
    - A calha é uma peça só em U **sem faces por dentro** (paredes sem face de baixo, fundo só com face de cima entre as paredes, sem tampas nas pontas): vista pelas pontas cortadas na borda da célula, as faces de dentro sobrepostas piscavam.
    - O eixo do parafuso dentro do encaixe é cortado exatamente na junta (passava 0,4 px e sobrepunha-se à hélice do parafuso de baixo).
    - Hélice com raio 4,3 px (0,3 px acima do fundo), bolsas de água afastadas da pá e das paredes, cintas de ferro, braço/punho da manivela, pontas de eixo e bica sem faces encostadas no mesmo plano.
  - **Sem malhas a atravessar-se** (verificado triângulo a triângulo, com os blocos montados e em várias fases da rotação):
    - As **bolsas de água** seguem a superfície da pá (a face de trás é a hélice, torta) e ficam abaixo do eixo: a água fica entre a pá e a sua superfície, sem atravessar o eixo nem a pá.
    - As cintas de ferro são **arcos** que passam por cima da hélice (0,3 px acima do ponto mais alto dela); antes a travessa de cima era atravessada pela pá.
  - **Sem granulado a piscar ao longe**: a textura dos modelos tem **mipmaps** como os blocos do jogo (antes era só "nearest": de longe cada pixel do ecrã apanhava um texel ao acaso e cintilava). Para os ladrilhos do atlas não sangrarem uns nos outros, o glTF usa um atlas 128x128 com cada ladrilho 16x16 no meio de uma célula 32x32 e 8 px de margem que repete a borda. De perto continua pixel nítido. O jogo força o mesmo filtro (`engine/screw_view.py`).
  - Um pouco mais simples: hélice com 12 segmentos por volta e chapa de 1 px, arcos de ferro de 1 px, bolsas de água com menos faces.
  - O parafuso fica todo **acima da diagonal** do bloco: não entra no bloco de apoio por baixo nem no de trás. O modelo tem **prioridade de profundidade** sobre o bloco onde assenta (sem tremer onde as faces se tocam).
  - Cada bloco **ocupa uma célula**: sozinho, nada sai dela (calha e rotor cortados pelas faces da célula). Dois blocos em escada só se tocam numa aresta, por isso as **peças da junta** (`*_baixo` / `*_cima`) só aparecem com um vizinho ligado desse lado; as duas metades fecham a calha e o rotor **sem frestas**. A calha do encaixe só aparece com um parafuso ligado.
  - A hélice dá **2 voltas por bloco** (todos iguais, continua de um para o outro); todos os blocos de uma cadeia ficam na mesma fase da animação `girar`. As **bolsas de água** (translúcidas) aparecem quando levam água e sobem com as pás.
  - Os `.bbmodel` (em `assets/models/source/`) abrem no Blockbench com a animação `girar`; se os editar, exporte o glTF para `assets/models/produced_blocks/` mantendo os nomes dos grupos.
- **Receitas**: 2 parafusos = 3 madeira + 1 pedra + 15 almas; encaixe = 4 madeira + 2 pedra + 25 almas.

### Torque (no espírito do mod Create)

Rotação passada de peça em peça, com velocidade (RPM) e **força** (SU). Lógica em `engine/torque.py`.

- **Peças**:
  - **Eixo** (item 40; receita: 1 madeira → 4 eixos): barra de transmissão. Posto em cima/embaixo de algo fica **em pé**; numa face de lado fica **deitado** nessa direção (bloco `eixo deitado`). Passa a rotação de ponta a ponta, igual.
  - **Manivela** (item 39; 2 madeira + 1 pedra): fonte manual. Posta em cima de algo fica em pé; numa face de lado fica presa nessa face (`manivela de lado`). **Segure o botão direito** para girar (32 RPM, 256 SU); **Shift + botão direito** gira ao contrário. Para de girar ao largar o botão.
  - **Encaixe do parafuso**: consome força (`impacto` 2 + 1 por parafuso da cadeia, em SU por RPM) e passa a rotação ao parafuso (`relacao` 2). Liga-se pelos dois lados e por cima.
- **Ligações**: duas peças encostadas ligam-se quando ambas têm encaixe nessa face (eixo deitado: nas pontas; manivela: pela base). Ex.: encaixe ← eixo deitado ← eixo deitado ← manivela de lado; ou encaixe ← eixo em pé ← manivela em cima.
- **Força**: as manivelas a girar somam a capacidade; a rede gasta `impacto x RPM`. Se não chega (ex.: 1 manivela para mais de 6 parafusos), a rede **trava** e aparece *"Força insuficiente: a rede pede X SU e as manivelas dão Y"* — ponha outra manivela na mesma rede (a girar no mesmo sentido) ou encurte a cadeia.
- **Sentido**: é um vetor que passa pelos eixos; duas manivelas a girar a mesma rede em sentidos contrários **travam** (*"Duas manivelas a girar a rede em sentidos contrários"*). No encaixe, o sentido decide se a água sobe ou desce: manivela em cima com o botão direito sobe; num lado, depende do lado — se aparecer *"Assim a água desce"*, gire com Shift.
- **Física**: eixos e manivelas seguram-se na peça onde encaixam (não caem); os parafusos e o encaixe seguram-se na cadeia (o peso vai para o parafuso de baixo; ver *Peso / gravidade* acima). Uma peça sem nada a segurá-la cai como qualquer bloco.
- **Online**: quem gira manda `{t:"crank", cell, dir}` 4x por segundo enquanto segura o botão; o servidor confere que ali há uma manivela e que o jogador está perto, e passa aos outros. Cada jogo calcula as redes a partir dos blocos (é igual para todos); a água continua a ser simulada por um só.
- **Configuração** em `blocks.json`, `block_properties.torque` (`rpm`, `capacidade`, `impacto`, `impacto_por_parafuso`, `relacao`) e `variantes` (que bloco se põe em cada face).
- **Água como fonte**: um parafuso com água a descer gira a rede sozinho (ver *Água a descer* no parafuso). Uma manivela a ser girada manda mais do que a água.
- **Próximas peças possíveis** (como no Create): engrenagens para mudar velocidade, caixa de engrenagens para virar a direção, roda d'água / moinho de vento como fontes automáticas.
- **Testes**: cada conta recebe uma vez 8 parafusos, 1 encaixe, 8 eixos e 2 manivelas.

### Quem simula (online)

A água acontece **nos jogos**, não no servidor:

- Só **um** jogo por sala simula: o do jogador de **menor id** ligado. O servidor diz quem é (`host` no `snap`); se ele sair, o seguinte continua de onde estava.
- Toda mudança local (simulação ou balde de qualquer jogador) vai ao servidor como `{t:"water", changes:[["x,y,z", nível]...]}`; o servidor passa aos outros. O anfitrião recebe os baldes dos outros e continua a simulação a partir daí.
- O servidor guarda a água da sala (`room.water`) e a das bolsas dos parafusos (`room.screws`), manda o estado inteiro a quem entra (`water_state` / `screw_state`; o `screw_state` vale também para quem simula) e **grava tudo no MongoDB**, coleção `room_water` (um documento por sala: `{ _id: sala, c: {"x,y,z": nível}, s: {"x,y,z": [a, b]}, updated }`). Grava no máximo a cada 3 s se mudou, quando a sala fica vazia e quando o servidor desliga (uma gravação de cada vez por sala, pela ordem, para uma foto velha não ganhar a uma nova); ao reabrir a sala a água volta onde estava. Os logs mostram quantas células: `[salas] X aberta (…, N células de água)` / `fechada (água gravada: N células)`.
- **Sair e voltar logo** (ou voltar enquanto a ligação antiga ainda não caiu, até ~30 s se o jogo fechou sem despedida): antes a sala podia ser fechada **com o jogador lá dentro** (o fecho começava com a sala vazia e acabava depois de ele entrar); daí em diante o servidor ignorava tudo o que ele mandava (água, blocos) e nada era gravado. Agora o fecho só tira a sala se continuar vazia depois de gravar. Sem água nenhuma o documento é apagado. Recomeçar/apagar a sala apaga a água também. Ao sair, o jogo manda as últimas mudanças antes do `bye`.
- **Sozinho** (sem servidor) a água e as bolsas vão para o `rooms.json` (`agua` e `agua_parafusos` da sala), a cada 10 s se mudou e ao sair.

## Dano por colisão no jogador

Só acontece **acima de 18 de velocidade atual**: **1 de dano**, mais **0,5 a cada +9** de velocidade (de 18 a 27 → 1; 27 → 1,5; 36 → 2; 45 → 2,5...), com a redução da armadura. Vale para:

- **Encontrão**: bater a correr num bloco, mob ou outro jogador (o alvo continua a levar a força cinética, como antes).
- **Queda**: bater no chão a cair (velocidade de queda). Teleportes (void, troca de sala) não contam.

Constantes em `player.py`: `COLLISION_DAMAGE_SPEED` (18), `COLLISION_DAMAGE_BASE` (1), `COLLISION_DAMAGE_STEP` (0,5), `COLLISION_DAMAGE_EVERY` (9).
- O número de dano aparece por cima do jogador; morrer assim diz "numa colisão" / "na queda".

## Números de dano

Só aparecem em **mobs, jogadores e alvos com vida (stand)**. Golpes em blocos já não mostram número: o dano guardado do bloco continua no Inspecionar (`F4`).

## Inventário: tecla `P`

O inventário (e o baú aberto) abre e fecha com **`P`** (antes `E`).

## Modelo do tronco (wood)

`assets/models/source/wood.bbmodel` (abre no Blockbench): o tronco do jogo como malha de 16 lados (raio 8 px, altura 16 px, base em y=0), com a casca nos lados e os anéis em cima/baixo, numa textura só de 32x16 (feita de `wood_sides.png` + `wood_top_bot.png`). Já convertido em `assets/models/produced_blocks/wood.gltf` (`python tools/bbmodel_to_gltf.py assets/models/source/wood.bbmodel assets/models/produced_blocks/wood.gltf` depois de o editar). O bloco `wood` **ainda é `cylindrical`**: o modelo está pronto para quando passar a `produced`.

## Maçãs

Modelo `assets/models/produced_blocks/maca.gltf`, convertido de `assets/models/source/apple.bbmodel` (o `apple.bbmodel` original subido 0,2 px para a base ficar em y=0, sem as imagens de referência). O `tools/bbmodel_to_gltf.py` agora também converte elementos **mesh** do Blockbench e elementos fora de grupos. Item `maca` (id 20) e dois blocos com o mesmo modelo: `maca` (no chão: a base **encosta no chão**) e `maca_pendurada` (na árvore: o topo encosta na folha; pegá-la dá `maca`; ao cair vira `maca`, `"ao_cair"`).

- **Precisam de muita luz**: a maçã só nasce debaixo de uma folha cujo sítio tenha luz **20 ou mais** (`MIN_LIGHT` em `engine/apples.py`). Na prática: uma **pedra solar** a ~10 casas, sem paredes no meio. O fragmento solar (16) não chega, e uma árvore no escuro não dá maçãs. No servidor a luz é calculada sem gráficos (`light_at` em `engine/lighting.py`).
- **Tempo aleatório**: cada árvore tem o seu tempo, sorteado de cada vez entre **35 e 95 s** (`GROW_MIN` / `GROW_MAX`, média ~1 min), por isso não nascem todas ao mesmo tempo.
- **Nascem nas folhas e caem logo**: aparece **1 maçã** debaixo de uma folha (folha com ar por baixo), que **cai imediatamente** até ao chão. Já não ficam penduradas. A árvore pára quando já há **4 maçãs no chão** à volta dela (raio 4, `MAX_ON_GROUND` / `AROUND`). Maçãs penduradas de mundos antigos (`maca_pendurada`) são convertidas e caem ao abrir a sala.
- **Quebrar folhas**: cada folha quebrada tem **20% de chance de largar uma maçã**, que cai ao chão (`"ao_quebrar": {"bloco": "maca", "chance": 0.2}` em `blocks.json`; serve para qualquer bloco). Folhas que somem ao tombar a árvore não contam; pegar com `Q` também não.
- **Não fazem parte da árvore**: pegam-se com `Q` (sem tirar a árvore), empurram-se e quebram-se como qualquer bloco; não são sólidas (passa-se por elas). Quebrar: **0,1**.
- **Queda**: todo bloco que cai bate no que está por baixo (dano = ½·m·v² × 25% / 100, com a velocidade da altura da queda a 9,8 m/s²; acumula como um golpe; o chão natural não sofre). Uma maçã a cair (0,2 kg) **quebra a maçã de baixo** (tenacidade 0,4 J).
- **Comer**: com a maçã na mão, **botão esquerdo** come uma e recupera **2 de vida** (`"consumivel": {"vida": 2}` em `itens.json`; qualquer item pode ser comestível assim). Com a vida cheia não come.
- Online quem faz nascer as maçãs (e a chance das folhas) é o servidor; os jogos só recebem os blocos.

## Iluminação por blocos

Como no Minecraft: o mundo é escuro (céu preto) e a luz vem de **fontes** que se espalham pelas células livres, perdendo 1 nível (0-15) por casa. Blocos opacos (cubos, troncos) não deixam passar: há sombra atrás de paredes, em buracos e debaixo de telhados. Sem fonte nenhuma fica a **luz ambiente fraca** (`AMBIENT = 0.16`, azulada). Cada face tem ainda um sombreado fixo (topo claro, lados um pouco mais escuros, base escura) e os cantos encostados a blocos ficam mais escuros.

- **A tocha foi removida.** A luz agora vai até **nível 30** (`MAX_LIGHT`, antes 15), por isso uma fonte forte ilumina até 30 casas.
- **Pedra solar** (bloco default, id 26, textura `pedra_solar.png`, um núcleo de sol): `"luz": 30`, o máximo, com o maior alcance. É sólida; quebra com 25. Na mão ilumina 18. Cada conta nova recebe 4.
- **Fragmento solar** (default, id 27, `fragmento_solar.png`: pedra escura com fragmentos de luz): `"luz": 16`, bem menos que a pedra solar. Quebra com 30. Na mão ilumina 10. **Queima ao contato**: encostar nele (de pé em cima ou ao lado) faz dano a cada **0,5 s**, a subir de **1 até 20** em **30 s** de contato seguido; largar zera a subida (`"dano_contato": {"inicio", "maximo", "tempo", "tick"}` no `block_properties`, serve para qualquer bloco; `_update_contact_damage` em `main.py`).
- **Pedra** (default, id 28, `pedra.png`): a mesma pedra sem a luz. Pesa **25** (peso de queda 0,3: uma pedra a cair fere bem menos); empurra com 8, quebra com **80**. É um bloco de **baixa interação física** (`"baixa_interacao": {"despertar": 50, "raio": 3}` em `blocks.json`, `engine/block_physics.py`):
  - **adormecida** (como nasce, como é posta, como vem na dungeon): não cai, não é empurrada, não tomba nem é esmagada (posta no ar, fica no ar). Os golpes só **acumulam dano** (10 s de memória, como os outros blocos);
  - ao chegar a **50 de dano acumulado** ela **acorda**: passa à física normal (o golpe que a acordou já conta: empurra, cai sem apoio, quebra com 80) e cria um **campo**: todas as pedras num **raio de 3** acordam também (num piso suspenso cai um disco de ~29 pedras);
  - **sem cascata**: as que acordam pelo campo não criam outro campo, e pedras a cair não acordam as adormecidas onde batem;
  - uma pedra acordada continua acordada quando se move; volta a adormecer se for apanhada e posta de novo (ou quando a sala reabre).
- **Sombras** (`shadow_field` em `engine/lighting.py`): depois de espalhar a luz de uma fonte de bloco, cada célula iluminada vê se tem **linha de vista** até à fonte (um raio pelas células, com numpy, a partir do ponto da superfície do bloco de luz mais perto). Se um bloco opaco está no meio, fica em sombra e recebe só 35% da luz (`INDIRECT`, a luz que "dá a volta"). Um pilar ou uma parede fazem sombra do lado oposto à luz. É calculado só quando a luz da fonte é (re)feita: **no frame não custa nada** (uma pedra solar: ~14 ms a mais, uma vez; na dungeon a abertura fica ~7% mais lenta e o resto igual). As luzes na mão não fazem sombra (mudam a cada passo); mobs e jogadores também não. `SHADOWS = False` desliga.
- **Luz na mão**: itens com `"luz_na_mao": N` em `itens.json` iluminam à volta de quem os segura, a andar. Vale também para os outros jogadores.
- Qualquer bloco pode emitir luz com `"luz": N` no `block_properties`; qualquer item com `"luz_na_mao": N`.

Como funciona (`engine/lighting.py`): a luz de cada fonte é calculada à parte (BFS) e só é refeita quando um bloco muda a menos de 16 casas dela. Os níveis da região à volta do jogador (96 x 96 células, toda a altura) vão para uma **textura 3D**; o shader padrão (`engine/fast_shader.py`) lê-a na célula ao lado de cada face, por isso chunks, modelos, mobs e jogadores ficam iluminados sem refazer malhas. Nomes, números de dano e barras de vida ficam sem sombra. As mãos em primeira pessoa usam a luz da célula da cabeça. Ajustes: `AMBIENT`, `AMBIENT_COLOR`, `LIGHT_COLOR`, `REGION_XZ` no topo de `engine/lighting.py`.

## Chão: pedra dura, terra e grama

**Tamanho dos mundos.** Mundos de jogadores novos têm **40 x 40** blocos (`PLAYER_WORLD_SIZE` em `server/src/http/app.js` e `backend/local.py`). Há uma **sala pública** (`room_publica`, "Praça pública") de **200 x 200** que todos podem visitar, com um **minoculus no meio** e uma praça sem árvores à volta (`praca_raio`). Tem também **7 lagos** (`"lagos": {"quantidade": 7, "raio": [4, 9]}` na sala; ver *Lagos*). Aparece também na lista de mundos do login (sempre, mesmo sem nunca ter entrado lá). O servidor cria-a sozinho ao arrancar (e ao listar os mundos) se não existir (`ensurePublicRoom` em `server/src/db.js`); no modo local é criada no `rooms.json` ao abrir o portal. Na primeira vez numa sala o jogador aparece perto do centro (na pública, ao lado do minoculus).

**Fora do mapa e o void.** O mapa gerado é 40 x 40 (altura 20), mas dá para **construir além dele em qualquer direção**: x/z sem limite e até 64 camadas acima; essas posições ficam gravadas na sala (`rooms.json` / Mongo) como qualquer bloco. A **gravidade vale para tudo**: um bloco sem nada por baixo cai. Só a **pedra dura flutua** (`"fixo": true` em `blocks.json`) e é ela que segura o mapa. Lá em baixo, na **camada -20**, fica o **void** (`VOID_Y` em `engine/chunk_room.py`), um "chão" onde tudo some. O céu é **preto**.
- Um bloco que cai até ao void **quebra e some** (com o que tinha dentro: baús, almas de minoculus). A queda vê-se: o bloco cai até lá e só então desaparece. Para construir para fora sem cair, apoie no que já existe por baixo ou use a cola.
- Blocos **inquebráveis** (ex.: minoculus) que caem no void **reaparecem numa coordenada aleatória dentro da área do mapa** (os 40 x 40, ou 200 x 200 na pública), por cima do chão, com as almas guardadas.
- O **jogador** que chega ao void vai para a **sala pública**; se já estiver nela, volta ao centro da praça. Ao regressar ao mundo de onde caiu, aparece no centro.

**Agachado não cai.** Com `Shift` (agachado) e no chão, o jogador não sai da borda de um bloco, como no Minecraft: desliza ao longo da borda (`_keep_on_edge` em `player.py`).

**Grama = terra com grama por cima.** A grama não é um bloco que se tenha no inventário: é uma variação da **terra**. Pegá-la com `Q` dá **terra** (`"drop": "terra"` em `blocks.json`). A camada de cima do chão já nasce com grama, e **terra exposta** (nada na célula de cima) **vira grama** depois de ~60 s (±30%; `GROW_SECONDS` em `engine/grass.py`). Pôr um cubo opaco em cima da grama tapa-a e ela volta a ser terra. Vale para terra colocada, chão cavado, blocos empurrados pela física e blocos mudados por outros jogadores (a troca grava na sala e vai para o servidor). Visual: topo `grama_topo.png`, lados `grama_lado.png`, base `terra.png` (`"texture-top"`, `"texture-bottom"`, `"texture-sides"`; qualquer cubo `default` pode usá-las). A terra tem textura nova (`terra.png`) e as duas têm ícone isométrico.

**Blocos que não se pegam.** `"coletavel": false` em `block_properties` impede o `Q`: as **folhas** não se pegam com a mão. Continuam a poder ser empurradas e quebradas.

**Blocos inquebráveis.** `"inquebravel": true` em `physics` faz o bloco nunca quebrar (golpes, encontrões, peso, quedas), mas ele continua a poder ser **empurrado** e **pego** com `Q`. O **minoculus** é assim (o F4 mostra "quebrar: inquebrável").

O chão de cada sala tem quatro camadas geradas: **1 de pedra dura** em y=0, cobrindo a base do mapa inteiro, **2 de terra** (y=1 e 2) e **1 de grama** (y=3); as árvores nascem por cima (tronco a partir de y=4). O número de camadas de terra está em `GROUND_LAYERS` no topo de `CRUD_room.py`. A pedra dura (`stone.png`) é fixa e tem tenacidade enorme (200000 J), por isso nenhum golpe a mexe, e tem `"collectable": false` em `blocks.json`, por isso também não sai com `Q`: cavar a terra do chão nunca abre caminho para o vazio. Há um item `pedra dura` para construir com ela (a conta de teste tem 16 no inventário).

Salas antigas são migradas ao abrir, uma vez (o campo `"layers"` em `rooms.json` guarda a versão, agora 3): tudo o que foi colocado, tirado ou colado sobe para continuar por cima da terra — 3 blocos numa sala de antes da pedra dura, 2 numa sala que já tinha pedra + 1 terra. Um jogador que volte com a posição antiga é empurrado para fora do chão pela correção de spawn. Com 3 camadas de terra a sala tem ~719 mil blocos (antes ~399 mil): o arranque demora quase o mesmo, mas usa ~100 MB a mais de memória.

## Desempenho (FPS)

O que mais pesava por frame eram as **colisões em malha** dos chunks: cada chunk perto do jogador tinha um `collider='mesh'`, que o Panda3D transforma num polígono de colisão por triângulo, e o `FirstPersonController` e o `mouse` testavam raios contra todos eles **a cada frame** (e o custo de reconstruir esses colisores aparecia como travadas ao partir/colocar blocos). Nada no jogo usa essas colisões — andar, pular, mira, coleta, colocação e golpes já usam a grade de blocos — por isso:

- `ChunkRoom.MESH_COLLIDERS = False` (em `engine/chunk_room.py`): os chunks deixam de criar colisores. Para voltar ao comportamento antigo, ponha `True`.
- Em `main.py`, `player.traverse_target` e `mouse.traverse_target` apontam para uma entidade vazia (`sem_colisores`), para os raios internos do Ursina não percorrerem a cena inteira.

Meta de 80 FPS — o que mudou para isso:

- **Sem vsync, com teto de FPS** (`VSYNC = False`, `FPS_LIMIT = 144` no topo de `main.py`). Com vsync o jogo nunca passa da taxa do monitor (60 FPS num monitor de 60 Hz). `FPS_LIMIT = 0` tira o teto.
- **Terreno mais leve de desenhar.** As malhas dos chunks (terra, pedra, troncos, folhas) eram ~500 `Entity` que o Ursina percorre a cada frame e procura numa lista ao destruir, todas com transparência "dual" (duas passagens de desenho). Agora continuam `Entity`, mas criadas com `add_to_scene_entities=False` (o Ursina não as visita) e opacas (uma passagem). Há um modo experimental com nós simples do Panda3D (`ChunkRoom.LIGHT_NODES = True`), desligado porque no teste o terreno ficou invisível.
- **Malhas passadas em bloco.** Vértices, triângulos e UVs vão para o Ursina como arrays planos (numpy / listas de números), copiados direto para a placa em vez de tupla a tupla. Refazer um chunk com árvores caiu para ~4 ms de Python.
- **Partículas reaproveitadas.** Cada lasca era uma `Entity` nova, criada e destruída a cada golpe; agora ficam num estoque (escondidas e mostradas de novo) e fora da lista que o Ursina percorre.
- **Distância de visão 4 chunks** (`RENDER_DIST`, era 5: ~45% menos terreno para desenhar).
- **F3 (ou M) mede o FPS:** grava 5 s de jogo e escreve `perfil_fps.txt` na pasta **Downloads** (se não der, na pasta do projeto): FPS médio, frames lentos, quanto é Python, funções mais pesadas, número de entidades. A medição automática ao entrar está **desligada** (`AUTO_PROFILE_AFTER = 0` em `main.py`; ponha, por ex., 15 para voltar). O terminal mostra `[perfil] relatório salvo em ...`.

### O que o perfil (F3) mostrou e o que mudou

Primeira medição: 38 FPS, frame de 26 ms. Só ~3,5 ms eram do jogo; ~8 ms o desenho (Panda3D) e **~13 ms o próprio Ursina** a percorrer as ~770 entidades a cada frame:

- **Shader padrão sem entradas contínuas** (`engine/fast_shader.py`, ligado por `FAST_SHADER` em `main.py`). O shader de névoa do Ursina 8.3 fazia, em todo frame e para cada entidade, `camera.world_position` + 2 `set_shader_input` (~56 mil chamadas em 5 s). O novo calcula a distância à câmera no próprio vértice e lê a névoa de uma entrada posta uma vez na raiz da cena. Mesmo visual (e era também a causa do "mundo invisível" de antes: sem a posição da câmera, tudo ficava cor de névoa).
- **Entidades passivas fora da lista do Ursina** (`Game._prune_entities`, a cada 1 s): o que não tem `update`, `input` nem scripts deixa de ser visitado (continua a ser desenhado). Era o `has_disabled_ancestor` a subir pelos pais de cada entidade em todo frame.
- **Golpes de espada sem travar:** durante o golpe, a caixa da espada era testada contra um cubo 5x5x5 de células à volta de cada caixa (centenas de testes em Python por frame, ~50 ms). Agora só entram as células dentro da caixa que envolve a lâmina e o teste é feito para todas de uma vez com numpy (`boxes_hit_many`): mesmo resultado, ~40x mais rápido.
- **F4 sem picos:** os pontos das ligações percorriam as ~80 mil ligações e as etiquetas de resistência recalculavam tudo a cada 0,5 s (picos de ~90 ms). Agora olham só as células à volta e só refazem quando o jogador muda de bloco ou o mundo/as colas mudam.

### Forma dos blocos no mundo (`render`)

`block_properties.render` em `blocks.json` escolhe como o bloco é desenhado, sem mudar a física (`format` continua a valer para orientação, tombar etc.):

| `render` | Como fica | Custo |
|---|---|---|
| `cube` | cubo opaco; as faces encostadas noutro cubo somem | o mais leve |
| `cutout` | cubo com textura recortada (pixels transparentes), como as folhas do Minecraft; entre duas folhas desenha-se uma face só, visível dos dois lados | leve |
| `rounded` | cubo de arestas arredondadas | pesado (~9x mais vértices por face) |
| `cylinder` | cilindro de 16 lados | pesado (não esconde faces) |

Padrão atual: **tronco** = `cylinder` e **folhas** = `rounded` (o visual original; as folhas usam `ChunkRoom.LEAF_RESOLUTION = 2`). Para testar a versão leve: `"render": "cube"` no tronco e, nas folhas, `"render": "cutout"` com `"texture": "leaves_cutout.png"` (a `leaves.png` com os ~20% de pixels mais escuros transparentes). Num chunk com árvores isso leva de ~10 mil para ~5 mil vértices, mas no teste o ganho de FPS foi pequeno: as árvores não eram o gargalo.

**Texturas certas no que cai/tomba (online).** O servidor manda a animação **antes** das mudanças de bloco, e o jogo toca-a antes de as aplicar: a cópia que cai é feita com o bloco ainda no lugar (e os modelos produced — baú, maçã, tocha... — caem como um **modelo novo montado igual ao do chunk** (`ChunkRoom.build_produced_copy`, mesma textura e encaixe), em vez de uma cópia do modelo vivo, que virava um cubo sem textura quando o chunk ainda não o tinha criado). No tombo, cada tronco leva também a orientação **de antes** (o 5º valor de `solid`): antes a cópia começava já deitada e o tombo rodava-a mais 90°, com a casca e os anéis trocados.

**Tempo do tombo igual no servidor e no jogo.** A duração do tombo (`topple_duration(altura)` em `engine/topple_math.py`: ~1 s numa árvore de 4 blocos, ~1,5 s numa de 12) é a mesma conta da animação, por isso o servidor só manda os blocos para a posição final quando a animação acaba (antes os blocos apareciam caídos antes do tombo).

**Árvore a tombar como um objeto só.** As cópias de todos os blocos da estrutura vão para um único nó, que é o que gira (`ToppleBody`): um cálculo por frame em vez de um por bloco. As cópias também ficam fora da lista de entidades que o Ursina percorre a cada frame.

### Tela de carregamento

Depois do login aparece uma tela de **Carregando** com barra de progresso (`client/loading_screen.py`). A geração da sala (~7 s) corre numa thread, para a tela não congelar; depois o jogo monta **todos os chunks à volta do jogador** antes de entrar (até `LOADING_FRAME_BUDGET` = 30 ms por frame, com a barra a andar), por isso o mundo já está inteiro ao entrar e não há travadas nos primeiros segundos. O jogador fica parado até a tela sumir. Os passos estão em `Game._load()` (um gerador: cada `yield` é um frame).

Travadas a cada golpe que mexe num bloco (medidas fora do jogo, numa árvore a tombar: ~85 ms → ~8 ms no frame pior):

- **Coletor de lixo do Python** (o maior culpado). Os dicionários da sala (~700 mil blocos) passavam a ser vigiados pelo coletor no primeiro bloco alterado, e cada passagem dele percorria tudo. `Game._freeze_world_for_gc()` (em `main.py`), chamado ao entrar na sala, marca-os e congela-os com `gc.freeze()`; o coletor deixa de os percorrer.
- **Gravação do `rooms.json`**: antes, cada bloco alterado copiava todas as salas no frame do jogo e o JSON indentado era escrito com o codificador lento. Agora as alterações só marcam a sala como alterada e uma thread grava no máximo a cada `LocalBackend.ROOMS_WRITE_DELAY` (2 s), em JSON compacto. Ao sair, o arquivo é gravado na hora e indentado (legível).
- **Reconstrução de chunks**: a malha dos cubos comuns (terra, pedra dura) de cada chunk é reaproveitada quando só mudam árvores, baús etc.; folhas e troncos são unidos com numpy em vez de um laço por vértice; a terra encostada na pedra dura não desenha faces escondidas; e as folhas usam `ChunkRoom.LEAF_RESOLUTION = 2` (era 4: ~2,7x menos vértices, quase igual à vista; ponha 4 para voltar).

## Física de blocos: estruturas, impacto e gravidade

`engine/block_physics.py`. Tudo acontece na grade de blocos.

**Blocos avulsos e estruturas.** Dois blocos encostados são só dois blocos avulsos. Uma **estrutura** são blocos **ligados** entre si: as **árvores** são estruturas naturais (cada bloco da árvore liga-se aos vizinhos da mesma árvore, gerado pela seed) e as construções do jogador viram estrutura com o item **`cola`** (um frasco na mão, `assets/models/ferramentas/cola/cola.obj`). A conta de teste tem 32 colas no inventário.

**Cola por face.** Botão direito com a cola põe cola **só na face clicada** do bloco, que fica **branca por 2 segundos** (e depois esmaece). Gasta 1 cola por face.
- Se já há um bloco encostado nessa face, os dois ficam ligados.
- Se a face está livre, ela **guarda a cola**: o próximo bloco posto encostado a ela fica colado. Assim dá para pôr um bloco **no ar**, preso só pela lateral: a cola **segura blocos na horizontal**, mas só **até o peso passar da resistência** (ver *Desabamento por peso*, abaixo): uma viga curta fica, uma comprida cai.
- As faces com cola à espera ficam em `"glue_faces"` no `rooms.json` (`"x,y,z|dx,dy,dz"`) e aparecem no F4 como pontos amarelos no meio da face. Se o bloco dono da face sai do lugar (quebrado, empurrado, pego com Q), a cola dessa face perde-se. **Todo bloco que a física mexe (empurrado, tombado, deslizado ou caído) perde a cola**: as ligações e as faces com cola dele somem.
- A cola agora também pega no chão natural: uma estrutura colada ao chão fica ancorada (um golpe forte só arranca o bloco atingido, nunca a estrutura inteira).

**Resistência e carga.** Cada bloco tem, em `blocks.json`, a **massa** (`peso`, kg) e, em `physics`, as resistências em unidades físicas (ver *Física: força (N), massa (kg) e energia (J)*):

```json
"peso": 60,
"physics": {"tenacidade": 2000, "estabilidade": 3528, "compressao": 11760}
```

- **Mover** = arrastar 1 m: atrito × m × g (terra de 15 kg: 88 J = 0,9 pontos).
- **Quebrar** = `tenacidade` x **`BREAK_FACTOR` (7)** (terra 2000 J → **140 pontos**: 23 socos, 13 golpes da espada do herói; tronco 210, pedra 560, baú 175, folhas 28).
- **Balanceamento** (`BREAK_FACTOR`, `HIT_MOVE_FACTOR`, `PUSH_MOVE_FACTOR` no topo de `engine/block_physics.py`; os blocos ficaram 4x mais leves, estes fatores dão a resistência sem depender disso): para um **golpe** arrancar/empurrar um bloco (ou tombar uma estrutura) é preciso **35x** a força de arrastar: terra/grama **30,9**, tronco 25,7, pedra 12,9, baú 15,4, folhas 2,6 (3,5x a versão anterior: nem o soco, 6,1, nem a espada do herói, 11, mexem a terra; é preciso força). **Empurrar agachado**, encostado (sem golpe): **6x** — terra 5,3 (1,5x a versão anterior: com a força base ainda dá, a andar). O Inspecionar mostra já estes números.

A resistência que vale num golpe é a **efetiva**, que muda com a **carga** (o peso por cima, em N), como no mundo real. A conta usa a carga em "terras" (carga / 588 N):

- **Mover = mover × (1 + 0,5 × carga/588)**: o peso prende o bloco ao chão (atrito), por isso a **base de uma pilha é a parte mais estável**, difícil de empurrar.
- **Quebrar = tenacidade / (1 + 0,5 × carga/588)**: o material já está comprimido, por isso a **base é a parte mais frágil** e parte com menos. Se ela parte, o que está em cima desaba. Numa árvore ou estrutura colada, o resto **tomba** para o lado do golpe, como ao cortar um tronco.

**Exemplo:** 5 terras soltas empilhadas.
- A de baixo (4 terras = 2352 N em cima) fica com mover 10,6 e quebrar 6,7. A espada (11) **parte-a** e a pilha desce.
- A de cima (sem carga) fica com mover 3,5 e quebrar 20. A espada **empurra-a**.

**Área (cola).** Blocos colados da mesma altura **dividem entre si** o peso que está por cima deles (tensão = força / área, `_layer_loads`): uma coluna em cima de uma base colada **2x2** pesa **1/4** em cada bloco da base, numa **3x3** **1/9**. Ex.: 4 pedras duras (4 × 250 kg = 9800 N) em cima de uma terra sozinha deixam-na com quebrar 2,1; em cima de uma base colada 2x2, 6,5; 3x3, 10,4. Além disso, quem segura um bloco **erguido** pela cola carrega o peso dele (`_hang_loads`).

(Os valores de cada bloco estão na tabela da seção *Física*.)

O `stand` (com `health`) não entra nesta física: usa a vida de entidade (é o único com barra de vida).

**Impacto: nada, move ou quebra.** Para **mover**, cada golpe (soco na mira ou a lâmina da espada a tocar o bloco) compara o `attack_damage` com as resistências **efetivas** do bloco atingido, na hora:

| Golpe | Resultado |
|---|---|
| menor que **mover** | não mexe (mas o dano acumula, ver abaixo) |
| entre **mover** e **quebrar** | um bloco **avulso** é **empurrado 1 casa** na direção do golpe (do jogador para o bloco, no eixo horizontal dominante), **levando à frente a fila de blocos soltos encostados** nessa direção. A força precisa é a **soma do mover de todos os blocos da fila**: 1 terra precisa de 3,5, 2 terras de 7, 3 de 10,6 (cada uma com o peso que tiver em cima). Se essa soma chega ao **quebrar** do bloco golpeado, a fila **nunca** se move: um golpe forte o bastante parte o bloco antes. A fila não passa por chão natural, blocos colados, modelos maiores que 1 bloco nem pela borda da sala. O que ficar sem apoio cai. Numa **estrutura** (árvore ou construção colada) mexe-se **a estrutura inteira, como um corpo só** — exceto se o golpe for numa **folha** (ou bloco com `"structural": false` em `physics`): aí só ela é arrancada ou partida, com a resistência dela sozinha, e a árvore fica de pé: se é mais **alta** do que comprida na direção do golpe, **tomba** (gira 90° sobre a aresta da base); senão **desliza** 1 casa. A árvore tomba e os troncos ficam soltos; a construção colada também **perde a cola** ao tombar, deslizar ou cair: chega ao destino como blocos soltos (a cola não sobrevive à física). Se não houver espaço para isso, só o bloco atingido é arrancado (e a cola dele rompe-se) |
| dano **acumulado** igual ou maior que **quebrar** | o bloco **quebra** e some (não vai para o inventário; para guardar um bloco use `Q`) |

**Dano acumulado (10 s).** Golpes no mesmo bloco somam: o bloco guarda o dano enquanto levar golpes e "sara" se passar **10 s** sem ser atingido (`DAMAGE_MEMORY` em `engine/block_physics.py`). Assim dá para partir madeira (3000 J = 30 pontos) à mão, com 5 socos seguidos (6,1 cada, com a força base); `pedra dura` (200000 J) continua impossível. O número que sobe do bloco mostra o total acumulado (branco) até ele mexer ou quebrar. Se o bloco for empurrado, o dano vai com ele.

**Socar blocos dói.** Com a mão vazia, cada soco num bloco tira vida ao jogador, conforme a tenacidade: `0,2 × (tenacidade / 100) / 30` (entre 0,05 e 1): folhas ~0,05, terra ~0,13, madeira 0,2, pedra dura 1. Para fixar um valor num bloco: `"physics": {"punch_damage": 0.5}` em `blocks.json` (0 = não dói). Morrer assim mostra "de tanto socar blocos".

Como a carga baixa as resistências, um bloco com muito peso em cima fica fácil de arrancar e pode até ser esmagado: uma terra com 2 terras por cima tem `quebrar` 10, e a espada (11) parte-a.

**Gravidade.** Um bloco avulso sem nada por baixo cai até apoiar. Uma estrutura só cai se **nenhum** dos seus blocos estiver apoiado em algo de fora dela, e cai **inteira**, como um corpo só (as ligações vão junto). O fundo do mundo apoia. A gravidade é verificada depois de golpes, de colocar e de coletar blocos (um bloco colocado no ar cai; para fazer balanços, cole-os). Os blocos já existentes que estejam no ar só caem quando algo à volta mudar.

**Desabamento por peso (sem golpe).** Sempre que algo muda por perto (bloco posto, tirado, caído ou movido), o jogo confere se o peso não passou da resistência do material, nas filas **horizontais** e **verticais** (`check_stability` em `engine/block_physics.py`):

- **Horizontal (cola):** um bloco sem nada apoiado por baixo, preso de lado pela cola, pendura-se no apoio mais próximo. Cada ligação do caminho aguenta um **momento** (peso em N × distância na horizontal até o apoio, em m) de no máximo `estabilidade` × `GLUE_HOLD` do mais fraco dos dois blocos. Se passar, **a cola rompe ali e o que estava pendurado cai**. Com `GLUE_HOLD = 1`: terra (3528 N·m, 588 N por bloco) e tronco (4704 N·m, 490 N) aguentam uma viga de **3 blocos** para fora (588 × (1 + 2 + 3) = 3528); o 4.º faz a viga cair. Uma ponte colada nas duas pontas reparte o peso pelos dois apoios: de tronco aguenta um vão de até 6 (construída a partir das duas pontas, que a meio já não aguenta mais de 3 de cada lado).
- **Pilha solta (sem cola):** uma pilha de blocos soltos **tomba sozinha** quando o peso em cima de um bloco passa de `estabilidade` × `STACK_FACTOR` (0,75). Terra (3528 N) aguenta 4 em cima: **5 terras empilhadas ficam, a 6.ª derruba a pilha**, que gira e cai deitada para um lado livre (com a mesma animação das árvores). Tronco (4704 N, 50 kg): 8 ficam, o 9.º derruba. Se não houver espaço para tombar (entre paredes), o bloco de baixo cede e a pilha desce. Pilhas **coladas** não tombam assim (a cola reparte o peso); para elas vale só o esmagamento abaixo.
- **Vertical (esmagamento):** o peso que um bloco segura (o que está empilhado em cima e o que pende dele) não pode passar de `compressao` × `CRUSH_FACTOR` (N). Se passar, **o bloco é esmagado** (some em lascas, não vai para o inventário) e o que estava em cima cai. Ex.: 5 pedras duras (5 × 2450 N = 12250 N) em cima de uma terra solta (compressão 11760 N) esmagam a terra. O chão natural nunca é esmagado.
- **Torre colada fina demais:** a cola deixa a estrutura rígida, mas ela **tomba inteira** (e perde a cola) se for alta demais para a largura da base: altura máxima = pilha solta do material × `GLUED_STACK_BONUS` (2) × lado menor da base. Terra: coluna colada **1x1 aguenta 10** de altura, base **2x2 aguenta 20**, **3x3 aguenta 30**. Estruturas coladas ao chão natural estão ancoradas e não tombam assim.
- Árvores em pé ficam de fora (as folhas penduram-se no tronco por natureza).

Para mudar o quanto as coisas aguentam: `LOAD_FACTOR`, `GLUE_HOLD`, `STACK_FACTOR`, `GLUED_STACK_BONUS` e `CRUSH_FACTOR` no topo de `engine/block_physics.py`, ou `peso`, `tenacidade`, `estabilidade`, `compressao` e `atrito`/`empurrar` de cada bloco em `blocks.json`.

**Exemplos** (espada do herói = 11, soco = 6,1, com 600 N). Terra sozinha (mover 3,5, quebrar 20): o soco já a **empurra** 1 casa (quem levanta 61 kg arrasta uma terra de 60 kg), e 4 socos seguidos partem-na; a espada arranca-a. Uma pilha solta de 3 terras: o bloco de baixo (carga 2 terras: mover 7,1, quebrar 10) é **partido** pela espada e os 2 de cima caem; o soco não o mexe; o do meio (mover 5,3, quebrar 13,3) e o de cima (3,5/20) são arrancados até pelo soco. A mesma pilha colada: cada camada tem a sua carga (a de baixo segura as de cima), e a espada **tomba a pilha inteira**, que fica deitada e já sem cola. Uma parede colada de 3 de comprido × 2 de alto: golpeada de lado tomba; golpeada de ponta (3 de comprido na direção do golpe) desliza 1 casa. A terra do chão não sai do lugar (tem terra à volta) e a pedra dura por baixo nunca se mexe. Folha solta (mover 0,3, quebrar 4): o soco e a espada partem-na. Árvore: o soco não faz nada; a espada **parte a base do tronco** (que, com o peso da árvore, quebra com ~9) e a árvore **tomba** para o lado do golpe: as folhas somem e os troncos ficam deitados.

**Animação** (`engine/physics_fx.py`). O mundo lógico muda logo; o que se vê são cópias com a **forma verdadeira** de cada bloco (tronco cilíndrico, folha arredondada, cubo texturizado), montadas uma vez ao carregar e depois só copiadas. A cópia nasce exatamente por cima do bloco original e os chunks são refeitos no ritmo normal do jogo (nada é refeito de uma vez, o que travava). No fim, a cópia **espera o bloco verdadeiro aparecer por baixo e esmaece** (`Fader`, 0,22 s), por isso a passagem de "a cair" para "bloco na grade" é suave. Nos blocos `produced` (baú, banco, stand...) a cópia é o **próprio modelo glTF**, com textura e pose (`make_produced_proxy`): o original some no mesmo instante e, ao pousar, a cópia é trocada pelo modelo novo no frame em que o chunk o mostra, sem esmaecer (antes a cópia era um cubo de cor lisa e o modelo parecia piscar). As ligações gravam-se aos poucos (só as que a seed não recria), sem percorrer as ligações das árvores.

- **Tombar:** a estrutura cai como **um corpo rígido**, folhas incluídas, girando sobre a aresta da base com a física de uma barra a tombar (`θ'' = 3g/2L · sin θ`): começa devagar, acelera, bate no chão (~1 s numa árvore), **quica** duas vezes e assenta. No impacto as **folhas desfazem-se em partículas e esmaecem** e os troncos ficam deitados na grade, com um pouco de poeira. A rotação é feita no espaço do Panda, sem depender dos eixos do Ursina. Ajustes: `TOPPLE_G`, `TOPPLE_KICK`, `BOUNCE` em `physics_fx.py`.
- **Empurrar** desliza o bloco (desacelerando); **cair** acelera com a gravidade.
- **Golpes** soltam lascas na cor média da textura do bloco; **quebrar** solta mais.

**Gravação.** Blocos movidos/quebrados vão para `modifications`; as ligações que a seed não recria (cola e árvores que caíram) vão para `"links"` em `rooms.json` (`"x,y,z|x,y,z"`).

**Animação.** Quando uma cópia em movimento pousa e o bloco cai logo a seguir (ex.: tombou para fora de uma beirada, ou foi empurrado para o vazio), a cópia segue direto para a queda, sem ficar um "fantasma" parado onde pousou. Os chunks com blocos alterados são refeitos até 3 por frame (`ChunkRoom.DIRTY_PER_FRAME`), para o bloco original sumir logo que a cópia começa a mexer.

**Inspecionar (`F4`).** A parte de baixo do painel explica o **bloco mirado**:

| Linha | O que é |
|---|---|
| Bloco mirado | tipo e estado: solto, colado (n blocos), árvore, chão natural |
| Força p/ mover | o "mover" efetivo agora (e o do material, entre parênteses) |
| Força p/ quebrar | o "quebrar" efetivo agora (e o do material) |
| Peso em cima | a carga: quanto mais, mais estável e mais frágil |
| Seu golpe | o que o **seu golpe atual** faria daqui: `QUEBRA`, `EMPURRA n bloco(s)` (com a força que a fila pede), `mexe a estrutura inteira`, ou `nada` e porquê (ex.: `fila de 3 precisa 21 (nunca move)`) |

Além disso, as ligações num raio de 12 blocos aparecem como **pontos amarelos** e cada bloco visível num raio de 5 blocos ganha uma **etiqueta** com mover/quebrar efetivos (`show_values` em `engine/block_physics.py`; raio em `LABEL_RADIUS`).

**Chão natural.** As camadas geradas (pedra dura e as 3 de terra) são terreno compactado: o peso das outras camadas não as deixa mais frágeis, por isso têm sempre a resistência do material (terra: quebrar 20) e nunca são empurradas. A espada também só acerta blocos com alguma face à mostra: não atravessa o chão para partir as camadas de baixo.

## Portal: viajar para outros mundos (minoculus-grump)

Mire o `minoculus-grump` e clique com o **botão direito**: abre o **Portal do Minoculus**, com duas listas: **Meus mundos** e **Mundos de outros jogadores** (nome do mundo, dono e quantos jogadores estão lá agora). O atual aparece marcado ("você está aqui"). Clicar no mundo de outro jogador pede acesso ao servidor (`POST /api/worlds/:id/join`), que o põe na sua lista, e viaja para lá: é assim que os jogadores se encontram. Por agora todos os mundos são públicos. Em cima aparece a **Sala pública** (de todos, 200 x 200). Clique num mundo para viajar; **"+ Criar novo mundo"** pede ao servidor um mundo novo (semente aleatória, 40 x 40), dá-lhe acesso a ele e viaja logo. `ESC` fecha.

**Renomear.** Nos seus mundos há o botão **Renomear**: digite o nome novo (até 32 letras), **Enter** ou **Gravar** grava, **Esc** cancela. Só o dono pode (`PATCH /api/worlds/:id {"nome"}`; no local, `rename_world` em `backend/local.py`); a sala pública não muda de nome.

- A viagem grava tudo no mundo atual (posição, inventário, mobs, baús), descarrega-o e carrega o outro com a tela de carregamento, sem voltar ao login. Ao voltar a um mundo, apareces onde estavas nele.
- Quem decide os mundos é o back-end: `list_worlds(sessão)` e `create_world(sessão, nome)` (ver `backend/base.py`). No local, os mundos novos vão para `rooms.json` (com `"nome"`) e o acesso para `players.json` → `link_room_properties`.
- Qualquer bloco `produced` pode ser um portal: `"portal": {"title": "..."}` no `block_properties`.
- **Loja**: o portal mostra a secção **Loja** com um botão por item de `"portal": {"loja": {"item": preço}}` (no minoculus: **pedra de transmutação, 250 almas**), pago com as almas que o jogador carrega. Sem almas suficientes o botão fica apagado. Online o jogo manda `{t:"buy", item}`; o servidor (`eco_buy` no worker) confere o preço no `blocks.json` dele, cobra no livro e dá o item (item fora da loja ou sem almas: recusado).

## Mobs (esqueleto viajante)

Os mobs **pertencem ao servidor (back-end)**. A posição, a vida, o alvo e o que cada mob está a fazer são decididos só em `backend/mobs.py` (`MobServer`), que tem o seu próprio relógio e avança a simulação em passos fixos (20 por segundo). O cliente nunca muda um mob: ele

1. conta ao servidor onde está o jogador (`backend.mob_sync`: posição, altura, largura, contador de golpes) e recebe de volta o estado de todos os mobs (posição, rumo, vida, animação e há quanto tempo ela começou) e os **eventos** (ex.: `player_hit` — a espada de um mob acertou-te, com o dano);
2. pede para golpear um mob (`backend.mob_attack`); o servidor confere a distância, vê se o escudo bloqueou, tira a vida e responde com `acertou`, `bloqueou`, `escudo_quebrou`, `morreu` ou `invalido`.

Hoje o "servidor" corre dentro do jogo (`LocalBackend`), mas as mensagens já são as de um servidor de rede: um `HttpBackend` só precisa de implementar `mob_sync`, `mob_attack` e `mob_spawn` (ver `backend/base.py`) e correr o `MobServer` do outro lado. Todos os mobs ficam registados na sala, em `rooms.json` → `"mobs"` (id, tipo, posição, rumo, vida), gravados a cada 5 s e ao sair: ao reabrir, continuam onde estavam.

O cliente (`client/mobs_view.py`) só mostra: suaviza o movimento entre duas mensagens, toca o clipe que o servidor mandou no instante certo e desenha a barra de vida (aparece depois do primeiro dano).

**Comportamento** (servidor): parado ou a passear perto de onde nasceu; vê o jogador a até `detect_range` blocos e persegue-o (corre de longe, anda de perto); a `attack_range` vira-se para ele e dá o golpe (`slash`). Quando o jogador golpeia por perto, tem `block.chance` de levantar o escudo (`begin_block` → `block` → `block_stop`). O escudo tem vida própria: bloqueios tiram-lhe vida e, a zero, ele quebra (`block_destroy`): o mob fica atordoado e leva mais dano (`broken_damage_mult`, 1,5x); o escudo volta passado `regen_after` s. Sobe degraus de 1 bloco, cai com gravidade, não atravessa blocos nem outros mobs, e não sai da sala. Novos mobs nascem perto do jogador (entre `spawn.min_distance` e `max_distance` blocos) até haver `spawn.max` vivos. **Por agora o nascimento automático está desligado** (`"spawn": {"max": 0}` no `mobs.json`); volte a pôr `3` para os esqueletos nascerem de novo (no online, reconstrua o servidor: `docker compose up --build`).

**Caixas de colisão** (vistas no Inspecionar, F4): **corpo** (amarelo), **espada** (vermelho) e **escudo** (azul). Saem da própria geometria do modelo e seguem a animação osso a osso (`engine/mob_rig.py`, só numpy: o servidor e o cliente fazem exatamente a mesma conta).

- **Espada**: durante o golpe (só entre `attack.window` segundos do clipe), se a caixa da espada tocar no jogador, o servidor manda `player_hit` (uma vez por golpe). O dano passa pela armadura do jogador, como os impactos.
- **Escudo**: com o escudo levantado, um golpe cuja linha (do olho do jogador ao centro do corpo do mob) atravessa a caixa do escudo é bloqueado. Bater por trás ou de lado passa.
- **Corpo**: é o que os golpes do jogador acertam (soco pela mira; espada pela caixa da espada do jogador, como nos blocos) e o que impede o jogador de atravessar o mob. Blocos a cair (dano de impacto) também ferem mobs.

**`mobs.json`** (um tipo por entrada):

| campo | o quê |
|---|---|
| `model` | glTF em `assets/models/` |
| `scale` | blocos do jogo por bloco do modelo (0,95 ≈ altura do jogador) |
| `health` | vida |
| `walk_speed` / `run_speed` | blocos/s |
| `detect_range` / `lose_range` / `run_range` / `attack_range` / `wander_radius` | blocos |
| `attack` | `clip`, `hitbox` (qual caixa fere), `damage`, `cooldown` (s), `window` ([início, fim] no clipe, s) |
| `block` | `chance`, `range`, `hitbox`, `shield_health`, `hold`, `raised_after`, `cooldown`, `regen_after`, `broken_damage_mult` |
| `animations` | estado do servidor → nome do clipe no glTF |
| `hitboxes` | `{"nome": {"bone": osso, "exclude": [ossos], "pad": px}}` |
| `spawn` | `max`, `interval` (s), `min_distance`, `max_distance` (blocos) |

**Modelo**: o `.bbmodel` original fica em `assets/models/mobs/esqueleto_viajante/` e o glTF é gerado por `tools/bbmodel_to_gltf.py`:

```
python tools/bbmodel_to_gltf.py assets/models/mobs/esqueleto_viajante/esqueleto_viajante.bbmodel assets/models/mobs/esqueleto_viajante/esqueleto_viajante.gltf
```

O conversor lê os cubos (box UV, as 3 texturas embutidas: esqueleto, espada e escudo) e os 8 clipes (`idle`, `walk`, `charge`, `begin_block`, `block`, `block_destroy`, `block_stop`, `slash`), amostrados a 30 quadros/s porque o Blockbench usa catmull-rom. Serve para os próximos mobs: modele no Blockbench, guarde o `.bbmodel`, converta e crie a entrada no `mobs.json`.

## Login, salas e back-end

Ao iniciar, o jogo mostra a tela de login (`client/login_screen.py`): usuário e senha, `Tab` troca de campo, `Enter` confirma. **Não há escolha de mundo**: entra direto na sala onde estava (ver "Onde o jogador aparece"). A conta de teste em `players.json` é `teste` / `teste`.

O jogo nunca lê `rooms.json` nem `players.json` diretamente: tudo passa por um `Backend` (`backend/base.py`). A implementação atual, `LocalBackend` (`backend/local.py`), usa esses dois ficheiros e grava de forma atómica. Para usar o servidor real, basta criar um `HttpBackend` com os mesmos métodos (`login`, `load_room`, `set_room_block`, `reset_room_modifications`, `save_player`) e trocá-lo em `main.py`.

Ao colocar um bloco, as malhas dos chunks afetados são atualizadas pelo loop do jogo, no máximo um chunk por frame; instâncias glTF inalteradas são reutilizadas. As alterações de sala são guardadas em segundo plano e agrupadas para que snapshots antigos não acumulem. O jogo espera a última gravação de `rooms.json` ao encerrar.

- `rooms.json`: um objeto por sala (`room_<id>`) com `seed`, `size_x/y/z` e `modifications`. O formato antigo continua aceite (`"x,y,z": tipo`; `null` remove). Blocos orientáveis usam `"x,y,z": {"type": tipo, "orientation": valor}`. O terreno e as árvores são gerados pela seed; só as alterações ficam gravadas.
- Ao colocar um bloco `produced` (baú, banco ou criatura), a frente vira para o jogador e a orientação cardinal (`N`, `S`, `L`, `O`) fica guardada em `modifications`. O eixo do mundo é `N=-Z`, `S=+Z`, `L=+X`, `O=-X`.
- Blocos `cylindrical` acompanham a face clicada: numa face de cima/baixo ficam `cima`; numa face lateral X ficam `deitado_l_o`; numa face lateral Z ficam `deitado_n_s`. As pontas só são escondidas quando o vizinho preenche a célula inteira: outro cilindro que continua no mesmo eixo ou um cubo comum. Modelos `produced` (baú, stand, minoculus…), folhas arredondadas e cilindros perpendiculares não tapam a ponta (senão via-se o tronco oco, só meia casca). `cima` usa colisão cilíndrica; os troncos deitados usam colisão cúbica. Entradas antigas sem `orientation` mantêm o comportamento vertical e os `produced` apontam para `N`.

Exemplos de entradas orientadas:
```json
"12,3,8": {"type": "bau de madeira", "orientation": "L"},
"13,3,8": {"type": "wood", "orientation": "deitado_n_s"}
```
- `players.json`: um objeto por jogador (`#<id>`) com `usuario`, `senha_hash`, `nome`, `link_room_properties` (salas a que pertence), `spawn_point` (por sala), `properties_status` e `propertie_inventory`.
- `senha_hash`: guarda `pbkdf2_sha256$...`. Uma senha em texto puro (útil para criar contas à mão) é aceite uma vez e trocada por hash no primeiro login.
- O jogo salva posição (`last_position`, por sala) e inventário (`propertie_inventory`: `armor`, `slots` e `selected`) a cada 30 s e ao sair. No modo online, tudo isto vai para o documento do jogador na coleção `players` do MongoDB (`PUT /api/players/me`).
- **Ids dos itens.** Cada item de `itens.json` tem um `"id"` numérico (1 = elmo 1 ... 9 = terra, 12 = baú, 13 = minoculus, 15 = stand, 17 = cola, 18 = espada do herói). O inventário e os baús gravam o id, não o nome: `{"id": 9, "count": 64}` e, nos baús, `[9, 64]`. Item novo = id novo (não reutilize ids; ids repetidos são avisados no console). Registos com o nome (`"item": "terra"`) ainda são lidos. O formato antigo `itens` ainda é lido e é substituído por `slots` no primeiro salvamento.

## Formatos de bloco

Os tipos de bloco são definidos em `blocks.json`. Os formatos geométricos suportados são:

- `default`: bloco cúbico;
- `cylindrical`: bloco cilíndrico;
- `rounded`: bloco com geometria arredondada;
- `produced`: modelo externo com volume lógico configurável e animação opcional de tampa.

### Blocos `produced`

Coloque os modelos `.gltf`, `.glb` ou `.obj` em `assets/models/produced_blocks/` e as texturas em `assets/textures/`. O campo `model` aceita referências relativas a `assets/models/`, `assets/` ou à raiz do projeto (ex.: `"produced_blocks/wooden-chest.gltf"`). Se o caminho não existir, o jogo procura um ficheiro com o mesmo nome em qualquer subpasta de `assets/models/` e avisa no log onde o encontrou. Um `.obj` não tem animação: com `animation.mode` diferente de `none`, o log avisa e o bloco fica estático.

**Modelos maiores que o bloco** são encolhidos para caber nas suas células (`ChunkRoom._fit_produced`): se o modelo, já com `scale`, for mais largo ou mais fundo que o `size`, reduz por igual até ocupar 98% da célula, centra-o e mantém a base no mesmo sítio. Assim dois blocos encostados não se invadem (o baú tinha ~1,09 bloco de largura e dois baús lado a lado misturavam as texturas). Modelos que já cabem não mudam. Para desligar num bloco: `"fit": false` no `block_properties`.

Se um modelo tiver o eixo frontal diferente do esperado, configure `block_properties.rotation_offset` como `[X, Y, Z]` em graus. É um ajuste local do modelo, aplicado além da orientação cardinal de colocação; o padrão é `[0, 0, 0]`. O baú atual usa `[0, 180, 0]` porque o seu glTF foi exportado virado para trás.

#### Animação

`block_properties.animation` descreve apenas o comportamento da animação, sem escolher como ela é disparada:

- `{"mode": "event", "clip": "open", "close_clip": "close"}`: um evento executa o clipe uma vez; se existir `close_clip`, as execuções alternam entre os dois clipes.
- `{"mode": "continuous", "clip": "idle"}`: o clipe corre em loop enquanto o bloco está carregado.
- `{"mode": "none"}`: não aplica animações e não precisa de eventos. É o modo por omissão.

Os nomes de `clip` e `close_clip` têm de coincidir com os clipes do glTF. O reprodutor trabalha com canais `translation`, `rotation` e `scale` de nós, suportando interpolação `LINEAR` e `STEP`; morph targets e interpolação cúbica não são suportados. Em caso de clipe ausente, o log informa o caminho completo do modelo e os nomes de animação encontrados.

#### Eventos

Os gatilhos e ações ficam separados em `block_properties.events` e são tratados pelo pacote `events/`. Exemplo do baú:

```json
"animation": {"mode": "event", "clip": "open", "close_clip": "close"},
"events": [
  {"trigger": "key_press", "key": "p", "action": "toggle_animation"}
]
```

O `EventDispatcher` permite registar novos tipos de gatilho e de ação. Os matchers ficam em módulos próprios dentro de `events/triggers/` (o primeiro é `key_press`); um futuro trigger de colisão/proximidade pode ser adicionado ali, sem codificar tudo em `main.py` nem associar cada animação exclusivamente à tecla `p`. Para `key_press`, `key` pode ser uma tecla ou uma lista de teclas.

#### Os três tipos de bloco `produced`

| Bloco | `animation.mode` | Comportamento |
|---|---|---|
| `bau de madeira` | `event` | clipes `open`/`close` disparados pelo botão direito (`"key": "right mouse down"`) |
| `minoculus-grump` | `continuous` | clipe `animation` em loop sempre, sem eventos |
| `banco de madeira` | `none` | banco estático, sem clipes nem eventos (modelo `banco-de-madeira.gltf`; o nome antigo `old-stool` em saves continua a funcionar) |

Sem o campo `texture`, o bloco usa as texturas embutidas no próprio glTF (caso do `minoculus-grump`). Se o glTF não tiver geometria (por exemplo, uma exportação do Blockbench sem cubos), o jogo avisa no log e mostra o cubo magenta de reserva.

#### Stand (boneco de treino com vida)

O `stand` (`assets/models/produced_blocks/stand.gltf`) é um bloco `produced` que simula uma entidade: ocupa **1 bloco na horizontal e 2 na vertical** (`size: [1, 2, 1]`) e tem **50 de vida** com uma barra por cima da cabeça, que olha sempre para a câmera e mostra `vida/máximo`. Ao **bater nele** (botão esquerdo, dentro do range de ataque) leva o dano do golpe (`attack_damage` = força + arma: 1 com a mão, 11 com a espada) e toca a animação `animation.armor_stand.check_ground`, que recomeça a cada golpe. Com 0 de vida fica **desativado**: escurece, a barra fica cinzenta com "(desativado)", e não leva dano nem anima. **5 s sem golpes** depois do último, a vida volta a subir (25 por segundo) e, cheia, ele é reativado. A conta de teste tem 10 stands na hotbar (tecla `5`).

```json
"stand": {
  "model": "produced_blocks/stand.gltf",
  "block_properties": {
    "format": "produced", "offset": [0,-0.5,0], "scale": 1, "size": [1,2,1],
    "animation": {"mode": "event", "clip": "animation.armor_stand.check_ground"},
    "health": {"max": 50, "regen_delay": 5, "regen_per_second": 25},
    "events": [
      {"trigger": "hit", "action": "damage"},
      {"trigger": "hit", "action": "toggle_animation", "restart": true}
    ]
  }
}
```

- `health` (`engine/block_health.py`) serve para qualquer `produced`: `max`, `regen_delay` (s sem golpes), `regen_per_second`, `bar` (`false` esconde a barra). A vida fica só em memória, pela posição do bloco (não vai para `rooms.json`).
- Trigger `hit` (`events/triggers/hit.py`): disparado pelo golpe do jogador no bloco mirado, com `payload.damage`. Aceita `"min_damage"`.
- Ação `damage`: tira `payload.damage` (ou `"amount"` fixo da definição).
- `toggle_animation` com `"restart": true` recomeça o clipe mesmo a meio; sem isso (como no baú) ignora novos eventos até o clipe acabar. Num bloco desativado não toca.
- O dano é aplicado no instante em que o golpe sai.

#### Banco de madeira

O `banco de madeira` (antes `old-stool`) usa `produced_blocks/banco-de-madeira.gltf`, sem evento nem clipe (`"animation": {"mode": "none"}`). Saves antigos com `old-stool` (inventário ou `rooms.json`) são lidos com o nome novo (`LEGACY_BLOCK_IDS` em `blocks/__init__.py` e `LEGACY_ITEM_IDS` em `items.py`). O `size` reserva células e participa nas colisões, na mira e na validação dos limites da sala; a colisão é uma caixa baseada nesse tamanho.

**Sentar (botão direito)**: mire o banco e clique com o botão direito. O jogador senta-se no assento, virado para o lado (alinhado ao banco) para onde estava a olhar: as duas pernas esticadas para a frente e afastadas em V (`PlayerSkin.SIT_LEG_SPREAD` = 14° para cada lado, `SIT_LEG_DROP` = 6° para baixo) e, sem item na mão, os braços no colo (a pose entra suavemente em 0,25 s). Sentado, o jogador não anda, mas continua a olhar à volta com o mouse (passado o limite do pescoço, o corpo roda no banco), a golpear e a abrir o inventário. Botão direito, `espaço` ou `Shift` levantam: o jogador fica de pé à frente do banco (ou num lado livre; sem nenhum, em cima dele). Se o banco for empurrado, tombar ou quebrar, o jogador levanta-se. Não há espaço para sentar se houver um bloco logo acima do banco, e o `Q` não pega o banco enquanto se está sentado nele.

O comportamento vem de `block_properties.seat` no `blocks.json` (qualquer `produced` pode ser um assento):

```json
"seat": {"back_offset": 0.25, "y_offset": -0.2}
```

- `y_offset`: sobe (+) ou desce (-) o jogador sentado, em blocos (`-0.2` = um quinto de bloco mais baixo). Use para ele não flutuar nem afundar no assento.
- `back_offset`: quanto o quadril fica atrás do centro do banco (as coxas vão para a frente).
- `height` (opcional): topo do assento em frações de meio bloco a partir do centro; sem ele, usa o topo real do modelo.

A pose é feita no código (`PlayerSkin._apply_sit_pose` em `player_advanced/skin.py`), por cima do clipe `idle`/`hold_block`: não precisa de um clipe novo no `player.gltf` e funciona com qualquer armadura, que acompanha os ossos.

#### Baú: guardar itens

O `bau de madeira` agora guarda itens. Mire o baú e clique com o **botão direito**: a tampa abre (clipe `open`) e aparece a interface do baú, com a grelha do baú em cima e o inventário do jogador em baixo (o equipamento some enquanto o baú está aberto). `P` ou `ESC` fecham a interface e a tampa (com o baú aberto, o botão direito divide pilhas, como no inventário) (clipe `close`).

- Clique esquerdo/direito, Shift+clique e `1`-`8` funcionam como no inventário; Shift+clique leva a pilha entre o baú e o inventário.
- O conteúdo fica em `rooms.json`, na sala, em `"containers"`: `{"x,y,z": [["terra", 12], null, ...]}`.
- Se a física empurrar ou derrubar o baú, o conteúdo vai junto; se o baú quebrar, os itens vão para o inventário do jogador (o que não couber perde-se, com aviso).
- **Baú cheio no inventário**: o `Q` pega o baú **com** os itens dentro. Baús não se acumulam (1 por slot); cada um leva o seu conteúdo (`data.container` = id em `containers.json` no modo local, ou na coleção `containers` do Mongo no modo online). Colocado de novo, o conteúdo volta para o bloco.
- Com o baú na mão e **sem bloco na mira**, botão direito abre o baú. Um baú não pode ser guardado dentro dele mesmo (volta para o inventário).
- **Tampa aberta e o bloco de trás**: aberta, a tampa entra na célula de trás. Enquanto o baú está aberto (e até a tampa acabar de fechar), ele é desenhado **por cima** dos vizinhos (`ChunkRoom.set_produced_priority`): o modelo passa para uma camada desenhada depois do mundo (uma segunda câmera igual à do jogo, com a profundidade limpa), por isso o bloco de trás não atravessa a tampa e as faces do próprio baú continuam na ordem certa. Ao fechar, volta para o mundo normal.

Configuração em `block_properties.container` (qualquer `produced` pode ser um baú):

```json
"container": {"slots": 24, "title": "Baú de madeira"}
```

`slots` vai de 1 a 32 (8 por fila).

#### Itens na mão de outro jogador ("física" do item na mão)

No modo online, o que outro jogador segura é um objeto sólido:

- Dá para **subir em cima** do bloco/item que ele segura e ser **carregado** quando ele anda. O mesmo vale para o **próprio jogador**: dá para ficar em cima da cabeça de outro e andar junto.
- Não se atravessa nem o corpo do outro nem o item na mão dele (empurra para o lado).
- **Botão direito** no item na mão do outro usa-o como se estivesse no chão: **baú** abre o conteúdo (os dois podem mexer; quem fechar por último é que fica gravado), **banco** senta-se nele e acompanha quem o segura (levanta sozinho se ele trocar de item), **minoculus** abre o portal.
- A caixa do item vem do modelo na mão (`held_box`, em `client/remote_players.py`); as caixas são refeitas a cada frame em `Game._refresh_dynamic_solids`.
- Golpes no item na mão de outro (ex.: stand) não são suportados.

#### Ícones

Os ícones das armaduras, do `minoculus-grump`, do `banco de madeira`, do `stand` e da espada foram gerados a partir dos próprios modelos (vista 3/4 de frente, com sombra por face e um contorno escuro de 1 px), em `assets/icons/`. Nas armaduras, cada peça é recortada e escalada para o maior lado ocupar 56 px num ícone de 64, centrada, para todas terem o mesmo tamanho.

O baú usa o `wooden-chest.gltf` exportado do Blockbench. Os clips `open` e `close` (0,75 s cada) animam o nó `top5`. A textura RGBA é aplicada como material opaco, sem a transparência dupla padrão do Ursina, para que o baú não desapareça. O botão direito inicia `open`; enquanto a transição corre, outro input é ignorado. Ao terminar, o último keyframe permanece aplicado e o modelo fica em repouso aberto. Ao fechar a interface do baú, toca `close` (se `open` ainda estiver a correr, espera ele acabar); ao terminar, a pose fechada fica estática. O estado aberto também é restaurado se o chunk descarregar/recarregar, mas não é guardado ao sair do jogo.

Configure as texturas e propriedades de cada bloco nesse arquivo.


### Nome do item ao passar o mouse

Com o inventário aberto (`P`), parar o mouse sobre um slot com item mostra uma caixinha com o **nome do item** ao lado do cursor (some enquanto há um item preso no cursor). Armaduras e armas mostram também uma segunda linha com os atributos do `item_balancing.json` (ex.: `Resistência +5`, `Dano x1.8 (kg que levanta / 10 x 1.8; 11 com 61 kg) · Alcance +1 · Vel. ataque 1.2`). Todo item com peso mostra também **`Peso: 25 kg`**: o peso real de **uma unidade sozinha** (o bloco no mundo, ou a armadura/arma inteira), não o que a pilha pesa no inventário nem o conteúdo de um baú (ex.: pedra 6,25 kg, terra 15 kg, fragmento solar 20 kg, baú grande 30 kg, pedra dura 250 kg). É o mesmo peso que conta na mochila e ao tirar o bloco com `Q`. O nome é o id do item com a primeira letra maiúscula; para outro texto, ponha `"nome": "Espada do Herói"` no item em `itens.json`.

### Números de dano

Um golpe que **muda alguma coisa** faz saltar o número do dano de cima do que foi acertado: sobe, abre um pouco para o lado e esmaece em ~0,85 s, sempre por cima dos blocos e virado para a câmera. Golpes que não fazem nada (bater no chão, num bloco preso) não mostram número. A cor diz o que o golpe fez: **amarelo** moveu/tombou/deslizou, **vermelho** quebrou, **laranja** alvo com vida (stand, sempre que leva dano). Ajustes no topo de `engine/damage_numbers.py`.

### Dano de impacto (desabamentos e projéteis)

Tudo o que cai ou tomba fere quem atingir:

```
dano = ½ x massa (kg) x velocidade² (m/s) x 25% / 100 J x IMPACT_FACTOR
```

- **Massa**: o `peso` do bloco (terra 15 kg, pedra 6,25 kg...).
- **Velocidade**: medida frame a frame pela posição do objeto no mundo, em blocos (= metros) por segundo, no instante em que toca no alvo. Como ½·m·v² = m·g·h, o dano cresce com a altura: uma terra que cai 1 bloco até à cabeça faz ~2,3; 2 blocos ~4,5; 4 blocos ~9 (a vida do jogador é 10). Uma pedra a cair 4 blocos faz ~3,8.
- Abaixo de `MIN_SPEED` (3 blocos/s) não fere. `IMPACT_FACTOR` (1,0) ajusta tudo de uma vez. Os dois ficam no topo de `engine/impacts.py`.
- **Quem leva dano**: o jogador e os blocos com vida (stand: recebe um evento `hit` com o dano, como num golpe). Um grupo que cai junto (pilha, árvore a tombar) atinge cada alvo **uma vez**: conta o primeiro bloco do grupo que toca.
- **Armadura**: reduz o dano recebido pelo jogador: `dano x 100 / (100 + armadura)` (100 de armadura = metade). Ajuste em `Player.ARMOR_HALF_AT`.
- Ao levar dano aparece um número vermelho por cima do jogador e a tela pisca em vermelho. Com a vida a 0 o jogador **morre** e volta ao ponto onde entrou na sala, com vida e estamina cheias.

**Outros projéteis no futuro** (flechas, pedras atiradas...): o sistema é genérico (`ImpactSystem` em `engine/impacts.py`). Quem dispara move a entidade como quiser e só faz:

```python
game.impacts.launch(flecha, peso, "flecha")   # começa a ferir
game.impacts.release(flecha)                  # parou / foi destruída
```

A velocidade é medida sozinha, por isso o dano já sai com a mesma fórmula. Novos alvos entram com `impacts.add_target(nome, caixa, ao_acertar)`.

### Vida e estamina

Acima da barra de itens há duas barras (`player_advanced/status_bars.py`): **vida** (vermelha, à esquerda; fica mais viva abaixo de 25%) e **estamina** (amarela, à direita), com o valor escrito por cima. A vida máxima é `vida_max` em `properties_status` (contas antigas usam a vida guardada).

A estamina (máx. 100) gasta **7 por golpe de arma**, **3 por soco** e **7 por segundo correndo** (~14 golpes seguidos ou ~14 s de corrida), e recupera **40 por segundo** depois de 0,4 s sem gastar (de 0 a cheia em ~2,5 s). **Zerada, o jogador fica exausto**: não golpeia e não corre, e anda 20% mais devagar, até a estamina voltar a 15 (meio segundo de descanso). A barra fica laranja e pisca enquanto isso. Valores em `STAMINA_*` e `EXHAUSTED_MULTIPLIER` no topo de `player.py`. O Inspecionar (F4) mostra vida e estamina.

**Range:** o alcance base do jogador passou de 2 para **3 blocos** (soco, colocar e coletar; `range_ataque` em `properties_status`, padrão `BASE_ATTACK_RANGE`).

## Defesa: vida, armadura e resistência mágica

Os **3 pilares da defesa** são a **vida** (quanto aguenta), a **armadura** (corta o **dano físico**) e a **resistência mágica** (corta o **dano mágico**). As duas cortam da mesma forma: dano × 100 / (100 + valor) — com 100, metade do dano (`ARMOR_HALF_AT`, `MAGIC_HALF_AT` em `player.py`). A resistência mágica vem do atributo `resistencia_magica` das peças vestidas (`item_balancing.json`, como a `resistencia`); nenhuma peça a tem ainda. O painel Status mostra **Armadura** e **Res. mágica**; o F4 mostra a linha **Defesa**.

Quase todo o dano é físico (golpes, quedas, encontrões, blocos, explosões). **Dano mágico**: um ataque, projétil ou explosão de mob com `"magico": true` em `mobs.json` (o servidor manda `magico` no `player_hit`). Por agora só o **orbe da Sombra** (o "mago negro") é mágico: a armadura não o corta, só a resistência mágica.

## Armadura

O inventário (`P`) tem 4 slots de equipamento acima da grelha: cabeça, corpo, pernas e pés. Arraste a peça para o slot certo (ou `Shift+clique`). As peças são itens de `itens.json` (`elmo 1/2`, `peitoral 1/2`, `calça 1/2`, `bota 1/2`; o slot de cada uma está em `ARMOR_ITEM_TO_SLOT`, em `items.py`) e o campo `model` aponta para o glTF em `assets/models/armaduras/armadura1|2/`. Armaduras têm de ser glTF: um `.obj` não tem ossos para seguir o corpo. `player_advanced/armor.py` carrega a peça e prende cada osso dela ao osso correspondente do `player.gltf`, por isso a armadura herda sempre a animação do corpo (andar, correr, pular). O `bipedWaist` das calças prende no osso `Waist` do jogador (com alternativas `Player Wide` e `Player`, para rigs anteriores). O equipamento é guardado em `propertie_inventory.armor` de `players.json`. A armadura só é visível em terceira pessoa (`F5`).


### Fazer uma armadura nova (sem animação)

**A peça não precisa de nenhuma animação.** O jogo prende cada grupo dela ao osso do jogador com o mesmo nome e ela segue todas as animações do corpo, inclusive as que ainda vão ser criadas. As armaduras 1 e 2 já estão assim: os seus `.gltf` não têm mais animações (passaram de ~400 KB para ~20–30 KB cada, com a mesma geometria e textura).

1. Abra no Blockbench o projeto-modelo da peça em `assets/models/armaduras/modelos_blockbench/`: `modelo_cabeca`, `modelo_corpo`, `modelo_pernas` ou `modelo_pes` (`.bbmodel`).
2. Ele traz o jogador de referência (grupo `REFERENCIA_nao_exporta`, travado e marcado para não exportar) e os grupos da armadura já com os nomes e pivôs certos, vazios: `bipedHead`; `bipedBody`, `bipedRightArm` › `bipedRightForearm`, `bipedLeftArm` › `bipedLeftForearm`, `bipedCape`; `bipedWaist`, `bipedRightLeg` › `bipedRightLowerLeg`, `bipedLeftLeg` › `bipedLeftLowerLeg`; `armorRightBoot`, `armorLeftBoot`.
3. Modele dentro dos grupos (pinte na textura `armadura.png` do projeto). Grupos que não usar podem ficar vazios.
4. Exporte como **glTF**, sem criar animações, para `assets/models/armaduras/armaduraN/cabeca.gltf` (ou `corpo`, `pernas`, `pes`) e aponte o item no `itens.json` para esse arquivo.

Se o grupo de referência acabar exportado por engano, o jogo apaga-o ao carregar a peça (nós `REFERENCIA*`/`ref *`).

**Capa sem animação.** Um grupo cujo nome comece por `bipedCape` (ou `cape`/`capa`), com o pivô na linha dos ombros, fica preso às costas e balança sozinho: um pouco parado, mais andando, bem mais correndo, levanta ao cair e cola às costas ao subir num pulo, com um balanço leve a cada passo (`_Cape` em `player_advanced/armor.py`; ângulos em `REST`, `WALK`, `RUN`, `AIR_UP`, `SWAY`). Os eixos são medidos na própria capa, por isso funciona com qualquer orientação do modelo.

## Balanceamento: `item_balancing.json`

Os números de jogo dos itens (resistência, peso e, futuramente, dano, velocidade etc.) ficam em `item_balancing.json`, separados da aparência (`itens.json`). Os itens são agrupados por categoria (`armaduras`, `armas`, `outros`), mas a busca é feita só pelo ID do item, o mesmo de `itens.json`:

```json
"armaduras": {
  "elmo 1": { "resistencia": 5, "peso": 2.5 }
}
```

| Peça | Resistência | Peso |
|---|---|---|
| elmo 1 | 5 | 2,5 |
| peitoral 1 | 13 | 6,0 |
| calça 1 | 6 | 3,5 |
| bota 1 | 3 | 1,5 |
| **conjunto 1** | **27** | **13,5** |
| elmo 2 | 8 | 3,5 |
| peitoral 2 | 19 | 8,5 |
| calça 2 | 9 | 5,0 |
| bota 2 | 4 | 2,0 |
| **conjunto 2** | **40** | **19,0** |

`item_balancing.py` lê o ficheiro (`get_stats`, `get_stat`, `sum_stats`). O `Player` soma as peças vestidas: `player.resistance`, `player.armor_weight` e `player.armor_stats` (todos os atributos somados). O total aparece no console sempre que uma peça é trocada. Por enquanto os valores só são calculados; ainda não alteram dano recebido nem velocidade. Qualquer atributo novo (ex.: `"dano": 7` numa arma) já é lido e somado sem mudar o código. Um atributo ausente vale 0. O ficheiro também é recarregado ao vivo, como o `itens.json`.

## Status e Inspecionar (`F4`)

O painel ficou mais estreito (`InspectPanel.WIDTH` = 0,78; era 1,1). Valores compridos encolhem sozinhos para caber na coluna.


O Inspecionar já não repete o que está no painel **Status** do inventário (força, velocidade base, estamina, peso, armadura, almas; o **nível** foi removido): mostra FPS, vida, **velocidade atual**, **força cinética**, range, dano e velocidade de ataque e o bloco mirado, incluindo o **dano guardado** (`12 / 30 (some em 7 s)`); o "seu golpe" já conta com esse dano.

A primeira linha é o **FPS**: média do último segundo e o **pior frame** em ms (as travadas aparecem aí; acima de ~33 ms nota-se). Com o F4 aberto o jogo desenha também as etiquetas e ligações, por isso o FPS pode ser um pouco menor do que com ele fechado.

`F4` abre e fecha o painel **Inspecionar** no canto superior esquerdo (`player_advanced/inspect_panel.py`). Ele só mostra informação: não solta o mouse nem trava o jogo, e os números atualizam sozinhos (ao trocar a armadura, por exemplo).

Todos os atributos vêm de `properties_status` em `players.json`, são aplicados ao `Player` ao entrar e gravados de volta a cada salvamento (`STATUS_FIELDS` em `client/player_state.py`):

| Chave em `properties_status` | No painel | Atributo do `Player` | Padrão |
|---|---|---|---|
| `vida` | Vida | `health` | 10 |
| `armadura` | Armadura | `base_armor` (+ armadura vestida = `total_armor`) | 0 |
| `velocidade` | Velocidade | `walk_speed` = velocidade **base**; o painel mostra a atual (`current_speed`) e a base, ex.: `17  (base 10, correndo +70%)` | 10 |
| `range_ataque` | Range de ataque | `attack_range` (em blocos, inteiro de 1 a 64) | 2 |
| `forca` | Força | `strength` | 1 |
| `peso` | Peso (no Status do inventário, em kg) | `base_weight` (+ armadura vestida = `total_weight`) | 40 |
| `attack_speed` | Velocidade de ataque | `attack_speed` (2 = golpes à velocidade normal; 3 = 1,5x) | 2 |

Na armadura e no peso, o painel mostra o total e a origem, ex.: `27  (0 + 27 equip.)`. Valores em texto ou número são aceites (`"3,5"` também); valores inválidos são avisados no console e ficam no padrão; chaves que faltam são criadas no próximo salvamento. Hoje `velocidade`, `attack_speed` e `range_ataque` mudam o jogo de facto; `vida`, `forca`, `armadura` e `peso` ficam guardados e visíveis, prontos para o sistema de dano.

### Range (alcance em blocos)

`range_ataque` é contado em blocos a partir do jogador: o bloco onde ele está vale **0**, o da frente **1**, o seguinte **2**. Com `range_ataque: "2"` o jogador alcança até 2 blocos à frente. Na diagonal conta o maior dos eixos, e na vertical as células do corpo (pés até a cabeça) valem 0, então o bloco sob os pés e o acima da cabeça valem 1 (`cell_distance_blocks` em `player_advanced/building.py`).

O mesmo alcance vale para o ataque e para **colocar** blocos (botão direito), **coletar** (`Q`), os eventos de bloco (botão direito no baú). O contorno preto que marcava o bloco mirado foi **removido** (só aparecia dentro do alcance e parecia ser preciso para bater); para o voltar a ver, `Interaction.SHOW_HIGHLIGHT = True`.

## Captura de tela (`F2`)

`F2` salva a imagem do jogo na pasta **Downloads** do utilizador como `jogo_AAAA-MM-DD_HH-MM-SS.png` (`client/screenshot.py`) e mostra "Captura salva" por 2 segundos (o aviso não aparece na foto). Nada é gravado dentro do projeto. No Linux usa a pasta de transferências do sistema (mesmo com nome traduzido); se não houver Downloads, grava na pasta pessoal. Funciona também na tela de login. As teclas ficam em `INSPECT_KEY` e `SCREENSHOT_KEY`, no topo de `main.py`.

## Controles de coleta

- **Q**: coleta o bloco que está sob a mira e o adiciona ao inventário. Se o inventário estiver cheio, o bloco permanece no mundo. **Blocos presos a uma estrutura** (colados, ou de uma árvore em pé) **não saem com Q** até ficarem soltos: partidos, arrancados quando a estrutura não tem espaço para se mexer (a cola rompe-se), ou com a árvore derrubada. Blocos com `"collectable": false` (pedra dura) nunca saem. Quando o Q é recusado, aparece um aviso curto no topo da tela.
- A remoção é gravada em `rooms.json` como uma modificação com valor `null`, preservando o estado ao reiniciar o jogo.
- **Botão direito** num bloco interagível: no baú abre a interface; no banco, senta/levanta; no minoculus, abre o portal; noutros blocos com evento `"key": "right mouse down"`, dispara o evento. Noutros blocos coloca o bloco da mão. **Shift + botão direito** coloca o bloco mesmo encostado a um bloco interagível.

### Animações de ações do jogador

O modelo `assets/models/player/player.gltf` fornece os clipes `hold_block` e `place_block`.
Quando há um item selecionado na hotbar, os braços ficam na pose `hold_block` (também ao andar, correr, pular, cair e agachar, só nos braços) e o item fica **sempre** visível nas mãos enquanto estiver selecionado; só some durante o soco.
Trocar de item, colocar um bloco (botão direito) ou coletar um bloco (`Q`) executa `place_block` uma vez.
As peças de armadura são presas aos mesmos ossos do jogador e acompanham automaticamente
essas animações, sem tocar clipes independentes.

## Modelo de itens e blocos

`blocks.json` define como cada bloco existe no mundo (textura, formato, colisão e propriedades). `itens.json` define os itens do inventário e sua representação visual na mão.

Cada bloco possui uma entrada de item com `"type": "block"` e `"block": "<id-do-bloco>"`. A coleta converte o ID do bloco para o ID do item; a construção converte o item selecionado de volta para o ID do bloco. Assim, os dois conceitos ficam separados mesmo quando usam o mesmo identificador por conveniência.

Exemplo de configuração de item de bloco:

```json
"terra": {
  "type": "block",
  "block": "terra",
  "icon": "terra.png",
  "model": "block",
  "hand": {
    "model": null,
    "position": [0, 0, 0.12],
    "rotation": [0, 0, 0],
    "scale": 0.4
  }
}
```

- `type`: `block` para item que coloca um bloco; `armor` para as peças de armadura; `item` para outros itens.
- `block`: ID em `blocks.json` que este item coloca no mundo.
- `model`: para armaduras, continua sendo o modelo equipado; para blocos, `block` indica que a representação usa a definição do bloco.
- `hand.model`: modelo específico da mão, ou `block` para usar a forma definida pelo bloco.
- `hand.position`, `hand.rotation` e `hand.scale`: posição, rotação e tamanho do modelo entre as mãos do jogador (ver "Item na mão").

Itens que não possuem bloco correspondente não precisam ter `block`. Os IDs antigos de blocos em inventários salvos continuam aceitos.

### Santuário do Anjo (sala de nível 3)

- **Sala nova** `room_santuario` ("Santuário do Anjo", `SANCTUARY_ROOMS` em `server/src/db.js`), criada sozinha ao arrancar o servidor. É **nível 3**: só aparece no minoculus de quem está na **Dungeon 1** (nível 2) — a regra de acesso por nível (`access.js`) é a mesma das dungeons.
- **Toda branca e vazia**: 80x80, geração `"branca"` (`worldgen.js` e `CRUD_room.py`, iguais) = só um chão liso de **pedra branca** (bloco novo: fixo e quase inquebrável como a pedra dura), sem árvores, lagos nem paredes. Minoculus no meio, para voltar à praça.
- **Céu branco e luz cheia**: campos da sala `"ceu": "#f4f5f8"` e `"ambiente": 1.0`; o jogo (`main.py`) usa-os para a cor do céu/névoa e para a luz ambiente (as outras salas continuam com céu preto e a noite escura).
- **O anjo**: `mobs.json` → `anjo.spawn.santuario` (1 de cada vez, nasce ao abrir a sala, volta 120 s depois de morto). A zona dos mobs vem do campo `"zona": "santuario"` da sala (`rooms.js`).


### Mini sóis e fadas (sala pública)

- **Mini sol** (bloco `mini sol`): `assets/models/fontes/mini_sol.bbmodel` (abre no Blockbench) → `produced_blocks/mini_sol.gltf`, ambos gerados por **`tools/gerar_mini_sol.py`** (núcleo amarelo, brilho laranja-claro, raios curtinhos; flutua a meio da célula). Luz 20, `"brilha"`. Nasce sozinho na grama da sala pública (`engine/tufts.py` `mini_suns`, no worker): ~16 num mapa 200x200, todos ao abrir. Pode ser partido; volta a nascer.
- **Fadas** (mob `fada`, `assets/models/mobs/fada/fada.gltf` de **`tools/gerar_fada.py`**: corpinho, cabelo, asas e uma bolinha de luz; animações idle/walk/death). **Saem dos mini sóis** (`spawn.publica.de_bloco: "mini sol"` — `spawnPoint` em `mobServer.js` escolhe um mini sol ao acaso), até 30 na sala, uma nova a cada 10 s quando morrem. Voam pelo mundo, não atacam, **4 de vida** (morrem fácil, dão 1 alma).
- **Brilham**: `"brilha": true` (modelo sempre claro) e `"luz": 10` — o jogo (`main.py` `_mob_lights`) põe uma luz que anda com cada fada (as 12 mais perto do jogador).

- **Ao entrar na sala** o servidor reenvia ao jogador os blocos que nascem sozinhos (capim, flores lunares, mini sóis — `NATURAL_BLOCKS` em `realtime/server.js`): os que nascem todos ao abrir a sala podiam chegar antes de o jogador estar lá e ficar invisíveis até relogar. O worker escreve no log `[plantas] flor lunar: N na sala` / `[plantas] mini sol: N` quando os põe (`docker compose logs`).

### Luz do fogo

Mais forte: cada grupo de fogo ilumina com nível **14 / 17 / 20** (era 8/10/12; fogo só no ar: 10, era 6) e até **10** luzes de fogo ao mesmo tempo (eram 6) — `engine/fire_view.py` `light_sources` e `main.py`.

### Desempenho (menos travamento)

- **Menos plantas**: capim até 1 por 50 blocos (era 25; 200x200 → 800), flores lunares 1 por 220 (era 110; → ~180). Uma sala que já tinha mais do que isso **perde as que sobram ao abrir** (`TuftGrowth`, uma vez; log `[plantas] capim: N tirados`).
- **Luzes mais baratas**: flor lunar luz 10 (era 16; continua desenhada sempre clara), mini sol 14 (era 20). As fadas: só as **4** mais perto iluminam (eram 12), com luz 7, e essas luzes só se refazem a cada **0,4 s** (`main.py` `_mob_lights`) — cada luz que anda é recalculada quando muda de célula.
- O capim voltou a ser feito de planos: o de cubinhos tinha milhares de faces por tufo.

### Fadas voam sempre

`mobs.json` `fada`: voam a ~3 blocos do chão a subir e descer (`voo.altura` 3, `bob` 0,8), mais rápidas (2,6) e quase sem parar — campo novo **`pensar`** (`[mín, máx]` s de pausa entre passeios; sem ele, 2-5 s como antes), lido em `wander` (`mobServer.js`).

### Plantas já na criação da sala pública

- A sala pública tem o campo **`plantas`** (`db.js` `PUBLIC_ROOM`): `{"capim": 0.008, "flor lunar": 0.0025, "mini sol": 0.0003}` = chance em cada bloco de grama. Saem **da geração do mundo** (como as árvores; `worldgen.js` `plantAt`/`plantCells` e `CRUD_room.py` `_generate_plants`, contas iguais — conferido: as mesmas 409 células nos dois), por isso já estão lá ao entrar (~300 capins, ~100 flores, ~12 mini sóis), sem travada e sem o servidor ter de as pôr.
- **Depois crescem mais com o tempo** (`engine/tufts.py`, no worker), devagar: já não há a leva de uma vez ao abrir a sala.
- Fora da praça do minoculus, dos lagos e da borda; onde há árvore, ganha a árvore.

### Fadas: correção

As fadas não nasciam: os blocos postos pelo servidor ficam gravados como `{type: ...}` e a procura dos mini sóis comparava com o texto. Agora a procura usa `world.get` e vê também os mini sóis da criação (`world.plants`) — `spawnPoint` em `mobServer.js`. O mesmo erro impedia o reenvio das plantas ao entrar na sala (`realtime/server.js`); corrigido.

### Berço Solar e fadas guardiãs

- O "mini sol" passou a chamar-se **Berço Solar** (bloco `berco solar`; os gravados como `mini sol` são traduzidos sozinhos — `LEGACY_BLOCK_IDS` em `blocks/__init__.py` e `LEGACY` em `roomWorld.js`). **Pode ser apanhado** (item `berco solar` em `itens.json`, ilumina na mão), é **muito duro** (tenacidade 60000) e **pesado** (180 kg).
- **Fadas nascem de cada berço, em qualquer sala e onde quer que esteja** (gerado com o mundo ou posto por um jogador): `mobs.json` `fada.ninho` → `nests()` em `mobServer.js`. Cada berço solta **1 a 3** logo que aparece e depois **mais uma a cada 20 s até ter 3**. Se o berço **for apanhado/partido, as fadas dele somem**. (Máximo de 120 fadas por sala.) Cada fada lembra-se do seu berço (`nest`, gravado com os mobs).
- **Guardiãs**: quem chega a **6 blocos** do berço é atacado; largam quando o jogador se afasta a 14 (ou continuam se foram atacadas). Ataque **corpo a corpo** ("investida", 2 de dano, a cada 1,2 s): descem à altura do jogador (`voo.altura_alvo`) e investem. 6 de vida.
- **Modelo novo da fada** (`tools/gerar_fada.py`): fada-duende de cabelo rosa com coque, orelhas pontudas, olhos grandes, vestido violeta e **4 asas de borboleta**, rasto de faíscas; animações idle, walk, **attack** (recua e investe) e death.

### Luz de blocos que se movem (sem piscar)

Quando um bloco que ilumina sai do sítio (empurrado, a cair), a luz dele fica mais **2 s** (`GHOST_TIME`, `engine/lighting.py`) ou até um bloco igual aparecer perto (onde ele caiu) — antes a luz apagava enquanto o bloco estava "no ar" e voltava a acender ao cair.

### Janela no Windows (`client/window_control.py`)

- **Tamanho certo da tela**: o jogo declara-se "DPI aware" antes de abrir a janela (com o Windows a 125%/150% a janela ficava do tamanho errado) e começa em **tela cheia sem bordas** do tamanho exato do monitor, por cima da barra de tarefas.
- **Barra de tarefas**: com o rato preso ao jogo, o ponteiro fica **confinado à janela** — clicar em baixo já não abre a barra do Windows.
- **Rato fora do jogo**: ao perder o foco (Alt+Tab, clicar noutra janela, tecla Windows) o rato é solto e o cursor do Windows volta ao normal; ao voltar, o jogo prende-o de novo.
- **F11**: alterna tela cheia sem bordas ↔ janela 1280x720 ao centro, com 0,8 s mínimo entre trocas (carregar muitas vezes já não estraga nada). O F11 próprio do Ursina foi desligado.
- **Teclas de aderência**: enquanto o jogo está aberto, os atalhos do Windows (Shift 5x = teclas de aderência, Shift 8 s = teclas de filtro, NumLock 5 s = teclas de alternância) ficam desligados; repõem-se ao fechar o jogo. Quem usa mesmo essas funções (ligadas) não é afetado.
