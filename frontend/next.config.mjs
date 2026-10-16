/** @type {import('next').NextConfig} */
// As chamadas /api/* passam por app/api/[...path]/route.ts (repasse com token, funciona local e na Vercel).
const nextConfig = { distDir: process.env.NEXT_DIST_DIR || ".next" };

export default nextConfig;
