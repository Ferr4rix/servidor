// O gerador do Python (random.Random(seed)) em JavaScript, bit a bit.
// O mundo base (onde ficam as árvores) é gerado pela semente da sala, e o
// cliente Python e o servidor têm de chegar exatamente às mesmas posições.
// Implementa: Mersenne Twister (MT19937), init_by_array como o CPython faz
// com uma semente inteira, getrandbits(k) e randrange(a, b).

class PyRandom {
  constructor(seed) {
    this.mt = new Uint32Array(624);
    this.index = 625;
    this.seed(seed);
  }

  seed(seed) {
    // CPython: a semente inteira (valor absoluto) vira uma lista de palavras
    // de 32 bits (menos significativa primeiro) para init_by_array.
    let n = BigInt(Math.abs(Math.trunc(Number(seed))));
    const key = [];
    if (n === 0n) key.push(0);
    while (n > 0n) {
      key.push(Number(n & 0xffffffffn));
      n >>= 32n;
    }
    this._initByArray(key);
  }

  _initGenrand(s) {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < 624; i++) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = (Math.imul(1812433253, prev) + i) >>> 0;
    }
    this.index = 624;
  }

  _initByArray(key) {
    const mt = this.mt;
    this._initGenrand(19650218);
    let i = 1, j = 0;
    let k = Math.max(624, key.length);
    for (; k > 0; k--) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(prev, 1664525)) + key[j] + j) >>> 0;
      i++; j++;
      if (i >= 624) { mt[0] = mt[623]; i = 1; }
      if (j >= key.length) j = 0;
    }
    for (k = 623; k > 0; k--) {
      const prev = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(prev, 1566083941)) - i) >>> 0;
      i++;
      if (i >= 624) { mt[0] = mt[623]; i = 1; }
    }
    mt[0] = 0x80000000;
    this.index = 624;
  }

  _uint32() {
    const mt = this.mt;
    if (this.index >= 624) {
      for (let kk = 0; kk < 624; kk++) {
        const y = (mt[kk] & 0x80000000) | (mt[(kk + 1) % 624] & 0x7fffffff);
        mt[kk] = mt[(kk + 397) % 624] ^ (y >>> 1) ^ ((y & 1) ? 0x9908b0df : 0);
      }
      this.index = 0;
    }
    let y = mt[this.index++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  getrandbits(k) {
    if (k <= 0) return 0;
    if (k > 32) throw new Error("getrandbits > 32 não suportado");
    return this._uint32() >>> (32 - k);
  }

  _randbelow(n) {
    const k = Math.floor(Math.log2(n)) + 1;   // n.bit_length()
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  randrange(start, stop) {
    const width = stop - start;
    if (width <= 0) throw new Error("randrange vazio");
    return start + this._randbelow(width);
  }

  random() {
    const a = this._uint32() >>> 5, b = this._uint32() >>> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
  }
}

module.exports = { PyRandom };
