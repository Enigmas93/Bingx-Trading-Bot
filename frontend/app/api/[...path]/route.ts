// Repasse de /api/* para o backend do bot (no PC). Roda no servidor do Next (local ou Vercel): o token do backend
// nunca vai para o navegador.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND_URL = (process.env.BACKEND_URL || "http://127.0.0.1:8010").replace(/\/$/, "");
const TOKEN = process.env.BOT_API_TOKEN || "";

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${BACKEND_URL}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "Content-Type": req.headers.get("content-type") || "application/json" };
  if (TOKEN) headers["X-Bot-Token"] = TOKEN;
  headers["ngrok-skip-browser-warning"] = "1"; // túnel ngrok: evita a página de aviso nas chamadas da API
  try {
    const res = await fetch(url, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(),
      cache: "no-store",
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch (e) {
    return Response.json({ detail: `backend do bot inacessível (${BACKEND_URL}): ${(e as Error).message}` }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
