import { NextRequest, NextResponse } from "next/server";
import { COOKIE, authEnabled, validToken } from "@/lib/session";

// Exige login quando DASHBOARD_PASSWORD está definida (painel acessível pela internet / Vercel).
const PUBLIC = ["/login", "/api/auth/login", "/manifest.webmanifest", "/sw.js", "/icon", "/apple-touch-icon", "/favicon"];

export async function middleware(req: NextRequest) {
  if (!authEnabled()) return NextResponse.next();
  const { pathname } = req.nextUrl;
  if (PUBLIC.some((p) => pathname.startsWith(p))) return NextResponse.next();
  if (await validToken(req.cookies.get(COOKIE)?.value)) return NextResponse.next();
  if (pathname.startsWith("/api/")) return NextResponse.json({ detail: "faça login" }, { status: 401 });
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!_next/static|_next/image).*)"] };
