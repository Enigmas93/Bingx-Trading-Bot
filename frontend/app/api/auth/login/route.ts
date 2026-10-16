import { NextRequest, NextResponse } from "next/server";
import { COOKIE, MAX_AGE, makeToken } from "@/lib/session";

export async function POST(req: NextRequest) {
  const { password } = await req.json().catch(() => ({ password: "" }));
  const expected = process.env.DASHBOARD_PASSWORD || "";
  await new Promise((r) => setTimeout(r, 400)); // freia tentativas em sequência
  if (!expected || password !== expected) return NextResponse.json({ detail: "senha incorreta" }, { status: 401 });
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, await makeToken(), { httpOnly: true, secure: req.nextUrl.protocol === "https:",
    sameSite: "lax", maxAge: MAX_AGE, path: "/" });
  return res;
}
