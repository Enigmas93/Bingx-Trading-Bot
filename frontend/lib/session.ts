// Sessão do painel: cookie assinado (HMAC-SHA256). Só ativa quando DASHBOARD_PASSWORD está definida.
export const COOKIE = "bot_session";
export const MAX_AGE = 60 * 60 * 24 * 30; // 30 dias

function secret(): string {
  return `${process.env.DASHBOARD_PASSWORD || ""}::${process.env.SESSION_SECRET || process.env.BOT_API_TOKEN || "bingx-bot"}`;
}

export async function sign(value: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret()), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function makeToken(): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  return `${exp}.${await sign(String(exp))}`;
}

export async function validToken(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  const [exp, sig] = token.split(".");
  if (!exp || !sig || Number(exp) < Date.now() / 1000) return false;
  const good = await sign(exp);
  if (good.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < good.length; i++) diff |= good.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

export function authEnabled(): boolean {
  return !!process.env.DASHBOARD_PASSWORD;
}
