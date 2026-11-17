"""Unidades físicas do jogo (Newton, quilo, Joule) e as regras que as ligam.

Régua: **1 bloco = 1 metro** (o jogador tem 1,8 blocos = 1,8 m). No mundo do
Ursina 1 bloco tem ``WORLD_PER_METER`` = 2 unidades, por isso uma velocidade
de 20 unidades/s são 10 m/s.

* **Força** (N): a força do jogador. Base 600 N, +60 N (+10%) por ponto de almas.
* **Massa** (kg): o ``peso`` de cada bloco/item (``blocks.json``,
  ``item_balancing.json``). Um número só para tudo (levantar, inventário,
  quedas, desabamento).
* **Levantar** (pegar blocos): massa máxima = força / g (600 N -> 61 kg).
* **Carga** (inventário + armadura): ``carry_capacity_kg`` — vida, um pouco de força.
* **Dano de um golpe**: ``weapon_damage`` = soco da força x multiplicador da arma x
  fator da carga (levando >= 70% do que levanta, o golpe enfraquece).
* **Energia** (J): é o que faz dano.
  - golpe (regra do jogo): **cada 10 kg que o jogador levanta = 1 de dano**
    no soco; a arma multiplica pelo "fator" dela (espada do herói 1,8).
    Energia do golpe = dano x 100 J;
  - algo a voar/cair que acerta alguém: E = 1/2 m v^2 x IMPACT_TRANSFER;
  - encontrão do jogador (a correr): E = 1/2 m v^2 x KINETIC_TRANSFER (8%),
    e mover um bloco assim gasta a mesma estamina que empurrar agachado;
  - queda do jogador: E = 1/2 m v^2 (m = corpo + armadura + carga); as
    pernas aguentam até SAFE_FALL_SPEED e absorvem 90% do resto;
  - empurrar um bloco 1 m: E = atrito x m x g x 1 m (força precisa = atrito x m x g);
  - quebrar um bloco: a "tenacidade" do material (J), somando golpes.
* **Vida** (pontos): 1 ponto = ``J_PER_HP`` = 100 J. O jogador (10 de vida)
  aguenta 1000 J; os números de dano na tela são pontos.
* **Desabamento** (N): carga = soma de m x g por cima; cada material aguenta
  uma ``compressao`` (esmaga) e uma ``estabilidade`` (pilha solta tomba /
  momento da cola em N.m, com 1 bloco = 1 m de braço).
"""

from __future__ import annotations

G = 9.8                    # m/s²
WORLD_PER_METER = 2.0      # unidades do mundo por bloco (= 1 m)
J_PER_HP = 100.0           # 1 ponto de vida/dano = 100 J

# Força do jogador (N)
BASE_STRENGTH_N = 600.0
STRENGTH_PER_POINT_N = 60.0    # +10%: +6 kg a levantar e +0,6 de dano no soco por ponto
KG_PER_DAMAGE = 10.0       # cada 10 kg levantados = 1 de dano no soco
PUNCH_FACTOR = 1.0         # fator do soco (as armas têm o seu em item_balancing.json)

# Carga (o que se leva no inventário + armadura vestida): vem sobretudo da
# VIDA (o corpo aguenta), um pouco da força. Com a vida base (10) leva 100 kg;
# cada ponto de vida +12 kg, cada ponto de força +4 kg. A força continua a
# decidir o que se LEVANTA (pegar blocos com Q), empurra e o dano. Até à
# carga o jogador anda à velocidade toda (Player.load_speed_factor).
CARRY_BASE_KG = 100.0
CARRY_BASE_HP = 10.0
CARRY_PER_HP_KG = 12.0
CARRY_PER_FORCE_POINT_KG = 4.0

# Corpo
BODY_MASS_KG = 40.0
SAFE_FALL_SPEED = 9.0      # m/s que as pernas aguentam sem dano
FALL_ABSORB = 0.9          # fração do resto que as pernas/rolar absorvem

# Impactos (blocos a cair/tombar, encontrões): fração da energia que passa
# para quem é atingido (o resto vai para o chão, deformação, ricochete).
IMPACT_TRANSFER = 0.25
# Encontrão do jogador (correr contra algo): o corpo não é um bloco rígido,
# amortece com braços/ombro, por isso passa bem menos. Calibrado para um
# jogador de 40 kg só arrastar um bloco de terra (3,5) a ~15 m/s, e para a
# carga não o tornar num aríete a andar.
KINETIC_TRANSFER = 0.08

# Blocos
DEFAULT_MASS_KG = 40.0
DEFAULT_FRICTION = 0.6     # atrito ao arrastar um bloco no chão
REF_BLOCK_N = 60.0 * G     # 1 bloco de terra (60 kg) pesa 588 N: referência

# Empurrar agachado: estamina gasta = trabalho (J) / isto (mín. PUSH_MIN_STAMINA)
PUSH_J_PER_STAMINA = 8.0
PUSH_MIN_STAMINA = 10.0


def lift_capacity_kg(force_n: float) -> float:
    """Massa que a força levanta."""
    return max(0.0, float(force_n)) / G


# Peso x golpe: levando 70% ou mais da **carga** (a linha "Peso" do status:
# o que leva / o que consegue levar, que vem da vida), o golpe enfraquece (o
# corpo está ocupado com a carga): -1% de dano por cada 1% acima de 70%, no
# máximo -60% (golpe a 40%). Levando 100%: -30%. Só mexe no **dano efetivo**
# (soco e armas), nunca na força; e não depende da força (antes era o que a
# força levanta: subir a força tirava o castigo do peso).
LOAD_DAMAGE_FROM = 0.70
LOAD_DAMAGE_SLOPE = 1.0
LOAD_DAMAGE_MIN = 0.40


def load_damage_factor(carried_kg: float, capacity_kg: float) -> float:
    """Multiplicador do dano pela carga: 1 até 70% da carga (``capacity_kg``,
    ver damage_load_capacity_kg)."""
    if capacity_kg <= 0:
        return LOAD_DAMAGE_MIN
    ratio = max(0.0, float(carried_kg)) / float(capacity_kg)
    if ratio <= LOAD_DAMAGE_FROM:
        return 1.0
    return max(LOAD_DAMAGE_MIN, 1.0 - (ratio - LOAD_DAMAGE_FROM) * LOAD_DAMAGE_SLOPE)


def damage_load_capacity_kg(max_health: float) -> float:
    """A carga contra a qual o peso enfraquece os golpes: a da linha "Peso"
    do status, só com a vida (a força não entra)."""
    return carry_capacity_kg(max_health, BASE_STRENGTH_N)


def weapon_damage(force_n: float, factor: float, carried_kg: float = 0.0,
                  max_health: float = CARRY_BASE_HP) -> float:
    """**Dano de um golpe** (pontos) = soco da força (kg que levanta / 10)
    x multiplicador da arma x o fator da carga (load_damage_factor, contra
    a carga da vida: a força não muda o castigo do peso)."""
    return strike_damage(force_n, factor) * load_damage_factor(carried_kg, damage_load_capacity_kg(max_health))


def carry_capacity_kg(max_health: float, force_n: float) -> float:
    """Quanto o jogador leva sem ficar mais lento do que metade (kg)."""
    extra_hp = max(0.0, float(max_health) - CARRY_BASE_HP)
    extra_force = max(0.0, float(force_n) - BASE_STRENGTH_N) / STRENGTH_PER_POINT_N
    return CARRY_BASE_KG + CARRY_PER_HP_KG * extra_hp + CARRY_PER_FORCE_POINT_KG * extra_force


def strike_damage(force_n: float, factor: float = PUNCH_FACTOR) -> float:
    """Dano de um golpe (pontos): kg que a força levanta / 10, x fator da arma."""
    return lift_capacity_kg(force_n) / KG_PER_DAMAGE * max(0.0, float(factor))


def strike_energy(force_n: float, factor: float = PUNCH_FACTOR) -> float:
    """Energia do golpe (J) = dano x 100."""
    return strike_damage(force_n, factor) * J_PER_HP


def kinetic_energy(mass_kg: float, speed_ms: float) -> float:
    return 0.5 * max(0.0, float(mass_kg)) * float(speed_ms) ** 2


def impact_energy(mass_kg: float, speed_ms: float) -> float:
    """Energia que um objeto a voar/cair passa para quem acerta."""
    return kinetic_energy(mass_kg, speed_ms) * IMPACT_TRANSFER


def body_impact_energy(mass_kg: float, speed_ms: float) -> float:
    """Energia que o jogador passa num encontrão (KINETIC_TRANSFER)."""
    return kinetic_energy(mass_kg, speed_ms) * KINETIC_TRANSFER


def fall_energy(mass_kg: float, speed_ms: float, force_n: float = BASE_STRENGTH_N) -> float:
    """Energia de uma queda que as pernas não aguentam (J). Pernas mais
    fortes aguentam mais: o resto divide-se por força / força base (quem
    levanta 10x mais aguenta uma queda 10x mais pesada)."""
    over = kinetic_energy(mass_kg, speed_ms) - kinetic_energy(mass_kg, SAFE_FALL_SPEED)
    legs = max(1.0, float(force_n) / BASE_STRENGTH_N)
    return max(0.0, over) * (1.0 - FALL_ABSORB) / legs


def to_hp(joules: float) -> float:
    return float(joules) / J_PER_HP


def push_force_n(mass_kg: float, friction: float = DEFAULT_FRICTION) -> float:
    """Força para arrastar o bloco no chão (atrito)."""
    return max(0.0, float(friction)) * max(0.0, float(mass_kg)) * G


def push_stamina(work_j: float) -> float:
    return max(PUSH_MIN_STAMINA, float(work_j) / PUSH_J_PER_STAMINA)
