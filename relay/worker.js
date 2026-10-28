// Relay for the dashboard's "Report an issue" button. The app has no GitHub login, so
// reports come here and this files them as issues with a token only the relay holds.
// Screenshots are kept in KV and served from /img/<id>, so the token needs no access
// beyond creating issues.
//
// Bindings (wrangler.toml): IMAGES (KV), LIMITER (rate limit, per IP); secret GITHUB_TOKEN.

export const REPO = "bc1224/WARLOGS";
export const LABEL = "from-app";
const MAX_TEXT = 4000;
const MAX_IMAGE = 4 * 1024 * 1024;
const PER_DAY = 40; // all reports together, so a flood can't bury the issue list
const IMAGE_TYPES = { png: [0x89, 0x50, 0x4e, 0x47], jpeg: [0xff, 0xd8, 0xff], webp: [0x52, 0x49, 0x46, 0x46] };

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});

// "data:image/png;base64,..." -> {type, bytes}, or null when it isn't a picture we take
export function decodeImage(dataUrl) {
  const m = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl || "");
  if (!m) return null;
  let bin;
  try { bin = atob(m[2]); } catch { return null; }
  if (bin.length > MAX_IMAGE) return null;
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  const magic = IMAGE_TYPES[m[1]];
  if (!magic.every((b, i) => bytes[i] === b)) return null;
  return { type: m[1], bytes };
}

// the report as an issue; the text is the reporter's own, so it goes in a fence that it
// can't close and @mentions can't ping anyone
export function issueFor({ text, version, page }, imageUrl) {
  const clean = text.replace(/\r\n?/g, "\n").trim();
  const first = clean.split("\n")[0].replace(/[`*_#<>\[\]@]/g, "").trim().slice(0, 70) || "Report";
  const body = [
    "Reported from the dashboard's Report an issue button.", "",
    "~~~~text", clean.replace(/~{3,}/g, "~~"), "~~~~", "",
    imageUrl ? `![screenshot](${imageUrl})` : "_No screenshot._", "",
    `Version: \`${String(version || "unknown").replace(/[^\w.\-]/g, "").slice(0, 40)}\``,
    page ? `Page: \`${String(page).replace(/[^\w#/\-]/g, "").slice(0, 40)}\`` : "",
  ].filter((l, i, a) => l !== "" || a[i - 1] !== "").join("\n");
  return { title: `From the app: ${first}`, body, labels: [LABEL] };
}

async function report(request, env, ctx) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  if (env.LIMITER && !(await env.LIMITER.limit({ key: ip })).success)
    return json(429, { error: "Too many reports from here. Try again in a few minutes." });
  if (Number(request.headers.get("content-length") || 0) > MAX_IMAGE * 1.4 + MAX_TEXT * 4)
    return json(413, { error: "That screenshot is too big (4 MB max)." });
  let r;
  try { r = await request.json(); } catch { return json(400, { error: "Bad request." }); }
  const text = typeof r.text === "string" ? r.text.trim() : "";
  if (text.length < 10) return json(400, { error: "Say a bit more about what happened." });
  if (text.length > MAX_TEXT) return json(400, { error: `Keep it under ${MAX_TEXT} characters.` });
  const img = r.image ? decodeImage(r.image) : null;
  if (r.image && !img) return json(400, { error: "The screenshot has to be a PNG, JPEG or WebP under 4 MB." });

  const day = new Date().toISOString().slice(0, 10);
  const count = Number(await env.IMAGES.get(`count:${day}`)) || 0;
  if (count >= PER_DAY) return json(429, { error: "Lots of reports today already. Try again tomorrow." });
  await env.IMAGES.put(`count:${day}`, String(count + 1), { expirationTtl: 2 * 86400 });

  let imageUrl = null;
  if (img) {
    const id = crypto.randomUUID().replace(/-/g, "");
    await env.IMAGES.put(`img:${id}`, img.bytes, { metadata: { type: img.type } });
    imageUrl = `${new URL(request.url).origin}/img/${id}.${img.type === "jpeg" ? "jpg" : img.type}`;
  }
  const res = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
      "user-agent": "wardogs-report-relay", "content-type": "application/json" },
    body: JSON.stringify(issueFor({ text, version: r.version, page: r.page }, imageUrl)),
  });
  if (!res.ok) return json(502, { error: "GitHub didn't take the report. Try again later." });
  const issue = await res.json();
  return json(200, { ok: true, number: issue.number, url: issue.html_url });
}

async function image(id, env) {
  const { value, metadata } = await env.IMAGES.getWithMetadata(`img:${id}`, { type: "arrayBuffer" });
  if (!value) return new Response("Not found", { status: 404 });
  return new Response(value, { headers: { "content-type": `image/${metadata?.type || "png"}`,
    "cache-control": "public, max-age=31536000, immutable", "x-content-type-options": "nosniff" } });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/report" && request.method === "POST") return report(request, env, ctx);
    const m = /^\/img\/([0-9a-f]{32})\.(png|jpg|webp)$/.exec(url.pathname);
    if (m && request.method === "GET") return image(m[1], env);
    return json(404, { error: "not found" });
  },
};
