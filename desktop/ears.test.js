// Feeds synthetic loudness into the island's hear() and checks when listening stops, and whether
// what it heard gets sent. Run: node desktop/ears.test.js
const fs = require("fs"), assert = require("assert");
const h = fs.readFileSync(`${__dirname}/ui/index.html`, "utf8");
const src = h.slice(h.indexOf("  function hear(r, d) {"), h.indexOf("  // Sends what it heard"));
const consts = h.match(/const QUIET_MS[\s\S]*?FLOOR_CHUNKS = \d+;\n/)[0];

// Listens to levelAt(seconds) for up to 40 s; returns when it stopped and whether it sent.
function listen(levelAt) {
  const clock = { t: 0 }, stops = [];
  const hear = new Function("clock", "stops", `
    let rec, deafUntil = 0;
    const info = {}, Date = { now: () => clock.t };
    const $ = () => ({ style: { setProperty() {} } });
    const loudness = (w) => Math.sqrt(w.reduce((s, v) => s + v * v, 0) / w.length);
    const stopListening = (send) => { stops.push({ at: clock.t / 1000, send }); rec = null; };
    const hint = () => {};
    ${consts}${src}
    rec = { chunks: [], levels: [], start: 0, last: 0 };
    return (d) => hear(rec, d);`)(clock, stops);
  const CHUNK_MS = 4096 / 48000 * 1000;
  for (clock.t = 0; clock.t < 40000 && !stops.length; clock.t += CHUNK_MS) {
    const a = levelAt(clock.t / 1000) * Math.SQRT2;
    hear(Float32Array.from({ length: 4096 }, (_, i) => a * Math.sin(i / 7)));
  }
  return stops[0];
}
const words = (t) => (Math.floor(t * 4) % 4 === 3 ? 0.03 : 0.15);  // talk, with short gaps
const show = (s) => s ? `stopped at ${s.at.toFixed(1)} s, ${s.send ? "sent" : "dropped"}` : "never stopped";
const cases = [
  ["fan, then a 3 s sentence", (t) => (t > 1 && t < 4 ? words(t) : 0.03), 5.3, true],
  ["talking straight away", (t) => (t < 3 ? words(t) : 0.03), 4.3, true],
  ["nobody says anything", () => 0.005, 7, false],
  ["steady music, nobody talks", () => 0.06, 7, false],
];
for (const [name, levelAt, at, send] of cases) {
  const s = listen(levelAt);
  console.log(`${name}: ${show(s)}`);
  assert(s && Math.abs(s.at - at) < 0.3 && s.send === send, name);
}
console.log("ok");
