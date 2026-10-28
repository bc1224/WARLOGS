// node --test relay/
import assert from "node:assert/strict";
import test from "node:test";
import worker, { decodeImage, issueFor } from "./worker.js";

const PNG = "data:image/png;base64," + Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]).toString("base64");

function env() {
  const kv = new Map();
  return {
    GITHUB_TOKEN: "test-token",
    IMAGES: {
      get: async (k) => kv.get(k)?.v ?? null,
      put: async (k, v, o) => kv.set(k, { v, m: o?.metadata }),
      getWithMetadata: async (k) => ({ value: kv.get(k)?.v ?? null, metadata: kv.get(k)?.m }),
    },
    LIMITER: { limit: async () => ({ success: true }) },
    kv,
  };
}

const post = (body) => new Request("https://relay.example/report", {
  method: "POST", body: JSON.stringify(body), headers: { "content-type": "application/json" } });

test("a report becomes an issue with its screenshot", async () => {
  const sent = [];
  globalThis.fetch = async (url, init) => { sent.push([url, init]); return Response.json({ number: 7, html_url: "https://github.com/x/7" }); };
  const e = env();
  const res = await worker.fetch(post({ text: "Round didn't log after I quit\nsecond line", image: PNG, version: "abc123" }), e);
  assert.equal(res.status, 200);
  assert.equal((await res.json()).number, 7);
  const [url, init] = sent[0];
  assert.equal(url, "https://api.github.com/repos/bc1224/warlogs/issues");
  assert.equal(init.headers.authorization, "Bearer test-token");
  const issue = JSON.parse(init.body);
  assert.equal(issue.title, "From the app: Round didn't log after I quit");
  assert.deepEqual(issue.labels, ["from-app"]);
  const img = /!\[screenshot\]\((https:\/\/relay\.example\/img\/[0-9a-f]{32}\.png)\)/.exec(issue.body)[1];
  const got = await worker.fetch(new Request(img), e);
  assert.equal(got.headers.get("content-type"), "image/png");
  assert.deepEqual([...new Uint8Array(await got.arrayBuffer())], [0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
});

test("the reporter's text can't break out of its fence or ping people", () => {
  const { title, body } = issueFor({ text: "@everyone ~~~~\n# hi", version: "v1; rm" }, null);
  assert.equal(title, "From the app: everyone ~~~~");
  assert.ok(body.includes("~~~~text\n@everyone ~~\n# hi\n~~~~"));
  assert.ok(body.includes("Version: `v1rm`"));
});

test("bad reports are turned away before GitHub", async () => {
  globalThis.fetch = async () => assert.fail("should not reach GitHub");
  assert.equal((await worker.fetch(post({ text: "short" }), env())).status, 400);
  assert.equal((await worker.fetch(post({ text: "long enough text", image: "data:image/png;base64,AAAA" }), env())).status, 400);
  const limited = env();
  limited.LIMITER.limit = async () => ({ success: false });
  assert.equal((await worker.fetch(post({ text: "long enough text" }), limited)).status, 429);
  const busy = env();
  busy.kv.set(`count:${new Date().toISOString().slice(0, 10)}`, { v: "40" });
  assert.equal((await worker.fetch(post({ text: "long enough text" }), busy)).status, 429);
});

test("only real pictures are taken", () => {
  assert.ok(decodeImage(PNG));
  assert.equal(decodeImage("data:image/svg+xml;base64,PHN2Zz4="), null);
  assert.equal(decodeImage("data:image/png;base64," + Buffer.from("GIF89a").toString("base64")), null);
});
