// Interview panel: who the clicked occupant is, what it decided and felt (decisions.jsonl,
// docs/AGENT_COGNITION.md), and a question box for the grounded interview server
// (POST /interview {run, agent_id, question}, proxied by vite.config.ts to 127.0.0.1:8771).
import type { Player, Run } from "./replay";

export type Reason = { text?: string; reason?: string; label?: string; status?: string; verified?: boolean; cites?: string[] };
export type DecisionRecord = {
  record_id: string;
  agent_id: string;
  t_s: number;
  tick?: number;
  model?: string;
  say?: string | null;
  source?: "model" | "fallback" | "rule";
  note?: string;
  action?: string;
  emotion?: Record<string, number>;
  decision?: { action?: string; exit?: string; reasons?: Reason[]; padm?: { why?: string } };
};
// cognition.serve citations: {tag: "[t=148, d_000124:p1]", tick: 148, t_s: 74, record_id, item}; t= is a tick.
type Citation = string | { tag?: string; tick?: number; t_s?: number; t?: number | string; ref?: string; id?: string; record_id?: string; item?: string; label?: string };
type Answer = { answer?: string; citations?: Citation[]; verified?: boolean | Reason[]; reasons?: Reason[] };

const INTERVIEW_URL = new URLSearchParams(location.search).get("interview") ?? "/interview";
const EMOTIONS = ["fear", "anxiety", "frustration", "hope", "concern"];
const VERIFIED_LABELS = new Set(["necessary", "jointly sufficient", "jointly_sufficient", "verified", "decisive", "sufficient"]);
const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector)!;

let current: { player: Player; run: Run; agent: number; onClose: () => void; items: { t: number; element: HTMLElement }[] } | null = null;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", content = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content) element.textContent = content;
  return element;
}

// Reason labels come from the interview's ablation re-runs (docs/AGENT_COGNITION.md).
function badge(reason: Reason): [string, string] {
  const label = (reason.label ?? reason.status ?? "").toLowerCase();
  if (reason.verified === true || VERIFIED_LABELS.has(label)) return ["badge verified", "Verified"];
  if (label === "untested") return ["badge stated", "Not tested"];
  return ["badge stated", "Stated, not decisive"];
}

function reasonList(reasons: Reason[]) {
  const list = el("ul", "iv-reasons");
  for (const reason of reasons) {
    const item = el("li");
    const [className, text] = badge(reason);
    item.append(el("span", className, text), reason.text ?? reason.reason ?? "");
    list.append(item);
  }
  return list;
}

function timeOf(run: Run, citation: Citation): { t: number; label: string } | null {
  const dt = run.doc.meta.dt || 0.5;
  const byRef = (ref: string) => {
    const record = run.decisions.find((item) => item.record_id === ref);
    if (record) return record.t_s;
    const event = run.doc.events.find((item) => item.ref === ref);
    return event ? event.t : null;
  };
  if (typeof citation === "string") {
    // "[t=148, d_000124:p1]": tick, record, evidence item. The record's own time wins over the tick.
    const tick = /t\s*=\s*(\d+(?:\.\d+)?)/.exec(citation);
    const ref = /\b([dm]_[A-Za-z0-9_]+)/.exec(citation);
    const item = /:(\w+)\]?$/.exec(citation);
    const t = (ref ? byRef(ref[1]) : null) ?? (tick ? Number(tick[1]) * dt : null);
    if (t === null || !Number.isFinite(t)) return null;
    return { t, label: [`${t.toFixed(1)} s`, item?.[1] ?? (tick ? null : ref?.[1])].filter(Boolean).join(", ") };
  }
  const ref = citation.ref ?? citation.record_id ?? citation.id;
  const tick = citation.tick ?? (citation.t !== undefined ? Number(citation.t) : undefined);
  const raw = citation.t_s ?? (ref ? byRef(ref) : null) ?? (tick !== undefined ? tick * dt : null);
  const t = raw === null || raw === undefined ? NaN : Number(raw);
  if (!Number.isFinite(t)) return null;
  return { t, label: citation.label ?? [`${t.toFixed(1)} s`, citation.item ?? ref].filter(Boolean).join(", ") };
}

function chip(player: Player, run: Run, citation: Citation) {
  const at = timeOf(run, citation);
  if (!at) return el("span", "chip chip-dead", typeof citation === "string" ? citation : JSON.stringify(citation));
  const button = el("button", "chip", at.label);
  button.type = "button";
  button.title = `Jump to ${at.t.toFixed(1)} s`;
  button.addEventListener("click", () => player.seek(at.t));
  return button;
}

// Inline citations such as "[t=312, p3]" or "[m_88 from a_09]" become chips in place.
function answerText(player: Player, run: Run, text: string): [HTMLParagraphElement, number] {
  const paragraph = el("p", "iv-answer-text");
  let chips = 0;
  let last = 0;
  for (const match of text.matchAll(/\[([^\]]+)\]/g)) {
    if (!timeOf(run, match[1])) continue;
    paragraph.append(text.slice(last, match.index), chip(player, run, match[0]));
    last = match.index! + match[0].length;
    chips += 1;
  }
  paragraph.append(text.slice(last));
  return [paragraph, chips];
}

function renderAnswer(player: Player, run: Run, data: Answer) {
  const box = $("#iv-answer");
  const [paragraph, inline] = answerText(player, run, data.answer || "No answer: the server kept no sentence it could verify.");
  box.replaceChildren(paragraph);
  if (data.citations?.length && !inline) {
    const chips = el("div", "chips");
    for (const citation of data.citations) chips.append(chip(player, run, citation));
    box.append(chips);
  }
  const reasons = data.reasons ?? (Array.isArray(data.verified) ? data.verified : null);
  if (reasons?.length) box.append(reasonList(reasons));
  else if (typeof data.verified === "boolean") {
    box.append(el("p", data.verified ? "badge verified" : "badge stated", data.verified ? "Every claim checked against the log" : "Some claims could not be checked"));
  }
}

async function ask(question: string) {
  if (!current) return;
  const { player, run, agent } = current;
  const box = $("#iv-answer");
  const submit = $<HTMLButtonElement>("#iv-form button");
  box.replaceChildren(el("p", "iv-status", "Asking..."));
  submit.disabled = true;
  try {
    const response = await fetch(INTERVIEW_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run: run.runId, agent_id: run.doc.agents[agent].id, question }),
    });
    if (response.status >= 500 || response.status === 404) throw new Error("offline");
    const data = (await response.json()) as Answer & { detail?: unknown; error?: string };
    if (!response.ok) {
      box.replaceChildren(el("p", "iv-status error", `The interview server refused the question: ${data.error ?? JSON.stringify(data.detail ?? response.status)}`));
      return;
    }
    renderAnswer(player, run, data);
  } catch {
    box.replaceChildren(el("p", "iv-status error", "Interview server offline. Start it with python -m cognition.serve (--port 8771), then ask again."));
  } finally {
    submit.disabled = false;
  }
}

function sourceLine(record: DecisionRecord) {
  const model = record.model?.includes("/") ? record.model : "the model";
  if (record.source === "model") return `Model · ${model}`;
  if (record.source === "fallback") return `Rules · ${model} was asked and this answer was not used${record.note ? ` (${record.note})` : ""}`;
  return "Rules · no model was asked";
}

function whoLine(info: { persona?: string; speed?: number } | undefined, runName: string, compared: boolean) {
  const persona = (info?.persona ?? "person").replace(/_/g, " ");
  const name = persona.charAt(0).toUpperCase() + persona.slice(1);
  const speed = typeof info?.speed === "number" ? `${info.speed.toFixed(2)} m/s` : "";
  return [name, speed, compared ? runName : ""].filter(Boolean).join(", ");
}

function thought(record: DecisionRecord | undefined) {
  const box = $("#iv-thought");
  if (!record) {
    box.replaceChildren(el("p", "iv-empty", "No decision yet. It is still waiting to choose."));
    return;
  }
  const why = record.decision?.padm?.why || record.action || "Chose without a written reason.";
  const source = el("p", record.source === "model" ? "iv-source model" : "iv-source", sourceLine(record));
  box.replaceChildren(source, el("p", "", why));
  if (record.say) box.append(el("p", "iv-say", `“${record.say}”`));
  if (record.decision?.reasons?.length) box.append(reasonList(record.decision.reasons));
}

function drawTimeline(player: Player, run: Run, agentId: string) {
  const list = $("#iv-timeline");
  const items: { t: number; element: HTMLElement }[] = [];
  list.replaceChildren();
  const rows: { t: number; title: string; detail?: string; say?: string | null; source?: string }[] = [];
  for (const record of run.decisions.filter((item) => item.agent_id === agentId)) {
    rows.push({
      t: record.t_s,
      title: record.action || "Decided",
      detail: record.decision?.padm?.why,
      say: record.say,
      source: sourceLine(record),
    });
  }
  for (const event of run.doc.events.filter((item) => item.agent === agentId && item.kind !== "decision")) {
    rows.push({ t: event.t, title: event.text ?? event.kind });
  }
  rows.sort((a, b) => a.t - b.t);
  for (const row of rows) {
    const item = el("li");
    const button = el("button");
    button.type = "button";
    button.append(el("time", "", `${row.t.toFixed(1)} s`), el("span", "what", row.title));
    button.addEventListener("click", () => player.seek(row.t));
    item.append(button);
    if (row.source) item.append(el("p", "iv-why", row.source));
    if (row.detail) item.append(el("p", "iv-why", row.detail));
    if (row.say) item.append(el("p", "iv-why", `Said: ${row.say}`));
    list.append(item);
    items.push({ t: row.t, element: item });
  }
  if (!items.length) list.append(el("li", "iv-empty", "Nothing recorded for this person yet."));
  const latest = run.decisions.filter((item) => item.agent_id === agentId).pop();
  thought(latest);
  return items;
}

export function openInterview(player: Player, run: Run, agent: number, onClose: () => void) {
  const info = run.doc.agents[agent];
  $("#iv-title").textContent = info.id;
  $("#iv-who").textContent = whoLine(info, run.name, player.runs.length > 1);
  $("#iv-answer").replaceChildren();
  const items = drawTimeline(player, run, info.id);
  current = { player, run, agent, onClose, items };
  $("#iv-body").hidden = false;
  $<HTMLButtonElement>("#iv-close").hidden = false;
  updateInterview(player.time());
}

/** Rebuild the open log after live decisions arrive. */
export function refreshInterview() {
  if (!current) return;
  const { player, run, agent } = current;
  const info = run.doc.agents[agent];
  $("#iv-who").textContent = whoLine(info, run.name, player.runs.length > 1);
  current.items = drawTimeline(player, run, info.id);
  updateInterview(player.time());
}

export function updateInterview(t: number) {
  if (!current) return;
  const { player, run, agent, items } = current;
  const state = player.stateAt(run, agent, t) || "not in the building";
  $("#iv-state").textContent = state === "exited" ? "evacuated" : state;
  $("#iv-time").textContent = `${t.toFixed(1)} s`;
  // Latest recorded emotion at or before t.
  const id = run.doc.agents[agent].id;
  const record = run.decisions.filter((item) => item.agent_id === id && item.t_s <= t).pop();
  thought(record);
  const emotion = $("#iv-emotion");
  if (!record?.emotion) emotion.replaceChildren(el("p", "iv-empty", run.decisions.length ? "No decision recorded yet." : "No decisions.jsonl next to this replay."));
  else {
    emotion.replaceChildren(
      ...EMOTIONS.filter((name) => record.emotion![name] !== undefined).map((name) => {
        const row = el("div", "bar");
        const fill = el("i");
        fill.style.width = `${Math.round(record.emotion![name] * 100)}%`;
        const track = el("span", "track");
        track.append(fill);
        row.append(el("span", "", name.charAt(0).toUpperCase() + name.slice(1)), track, el("output", "", record.emotion![name].toFixed(2)));
        return row;
      }),
    );
  }
  let active: HTMLElement | null = null;
  for (const item of items) {
    item.element.classList.toggle("future", item.t > t + 1e-6);
    if (item.t <= t + 1e-6) active = item.element;
  }
  for (const item of items) if (item.element !== active) item.element.classList.remove("active");
  if (active && !active.classList.contains("active")) {
    active.classList.add("active");
    const list = active.parentElement!;
    list.scrollTop = active.offsetTop - list.offsetTop - list.clientHeight / 3;
  }
}

$("#iv-close").addEventListener("click", () => {
  $("#iv-body").hidden = true;
  $<HTMLButtonElement>("#iv-close").hidden = true;
  $("#iv-title").textContent = "This person";
  $("#iv-who").textContent = "Click a person to read why they are moving.";
  current?.onClose();
  current = null;
});
$("#iv-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $<HTMLTextAreaElement>("#iv-question").value.trim();
  if (question) ask(question);
});
$<HTMLTextAreaElement>("#iv-question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $<HTMLFormElement>("#iv-form").requestSubmit();
  }
});
