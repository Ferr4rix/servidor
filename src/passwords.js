// Senhas no mesmo formato do cliente antigo (backend/passwords.py):
//   pbkdf2_sha256$<iterações>$<salt hex>$<hash hex>
const crypto = require("crypto");

const PREFIX = "pbkdf2_sha256";
const ITERATIONS = 200000;

function hashPassword(password, iterations = ITERATIONS) {
  const salt = crypto.randomBytes(16);
  const digest = crypto.pbkdf2Sync(String(password), salt, iterations, 32, "sha256");
  return `${PREFIX}$${iterations}$${salt.toString("hex")}$${digest.toString("hex")}`;
}

function isHashed(stored) {
  return typeof stored === "string" && stored.startsWith(PREFIX + "$");
}

function verifyPassword(password, stored) {
  if (typeof stored !== "string" || !stored) return false;
  const given = Buffer.from(String(password), "utf8");
  if (!isHashed(stored)) {
    const plain = Buffer.from(stored, "utf8");
    return plain.length === given.length && crypto.timingSafeEqual(plain, given);
  }
  const parts = stored.split("$");
  if (parts.length !== 4) return false;
  const [, iter, saltHex, digestHex] = parts;
  try {
    const expected = Buffer.from(digestHex, "hex");
    const digest = crypto.pbkdf2Sync(given, Buffer.from(saltHex, "hex"),
      parseInt(iter, 10), expected.length, "sha256");
    return digest.length === expected.length && crypto.timingSafeEqual(digest, expected);
  } catch (e) {
    return false;
  }
}

module.exports = { hashPassword, verifyPassword, isHashed };
