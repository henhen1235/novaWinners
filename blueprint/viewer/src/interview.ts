// Interview panel: who the clicked occupant is, what it decided and felt (decisions.jsonl,
// docs/AGENT_COGNITION.md), and a question box for the grounded interview server
// (POST /interview {run, agent_id, question}, proxied by vite.config.ts to 127.0.0.1:8770).
import type { Player, Run } from "./replay";

export type Reason = { text?: string; reason?: string; label?: string; status?: string; verified?: boolean; cites?: string[] };
export type DecisionRecord = {
  record_id: string;
  agent_id: string;
  t_s: number;
  tick?: number;
  model?: string;
  emotion?: Record<string, number>;
  decision?: { action?: string; exit?: string; reasons?: Reason[] };
};
type Citation = string | { tick?: number; t_s?: number; t?: number; ref?: string; id?: string; record_id?: string; label?: string; text?: string; verified?: boolean };
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

const isVerified = (reason: Reason) =>
  reason.verified === true || VERIFIED_LABELS.has((reason.label ?? reason.status ?? "").toLowerCase());

function reasonList(reasons: Reason[]) {
  const list = el("ul", "iv-reasons");
  for (const reason of reasons) {
    const item = el("li");
    const verified = isVerified(reason);
    item.append(el("span", verified ? "badge verified" : "badge stated", verified ? "Verified" : "Stated, not decisive"), reason.text ?? reason.reason ?? "");
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
    const tick = /t\s*=\s*(\d+(?:\.\d+)?)/.exec(citation);
    const ref = /\b([dm]_\d+)\b/.exec(citation);
    const t = tick ? Number(tick[1]) * dt : ref ? byRef(ref[1]) : null;
    return t === null ? null : { t, label: citation.replace(/^\[|\]$/g, "") };
  }
  const ref = citation.ref ?? citation.record_id ?? citation.id;
  const t = citation.t_s ?? citation.t ?? (citation.tick !== undefined ? citation.tick * dt : ref ? byRef(ref) : null);
  if (t === null || t === undefined) return null;
  return { t, label: citation.label ?? [`${t.toFixed(1)} s`, ref].filter(Boolean).join(", ") };
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
function answerText(player: Player, run: Run, text: string) {
  const paragraph = el("p", "iv-answer-text");
  let last = 0;
  for (const match of text.matchAll(/\[([^\]]+)\]/g)) {
    if (!timeOf(run, match[1])) continue;
    paragraph.append(text.slice(last, match.index), chip(player, run, match[1]));
    last = match.index! + match[0].length;
  }
  paragraph.append(text.slice(last));
  return paragraph;
}

function renderAnswer(player: Player, run: Run, data: Answer) {
  const box = $("#iv-answer");
  box.replaceChildren(answerText(player, run, data.answer ?? "(no answer text)"));
  if (data.citations?.length) {
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
    box.replaceChildren(el("p", "iv-status error", "Interview server offline. Start it with python -m cognition.serve (port 8770), then ask again."));
  } finally {
    submit.disabled = false;
  }
}

function drawTimeline(player: Player, run: Run, agentId: string) {
  const list = $("#iv-timeline");
  const items: { t: number; element: HTMLElement }[] = [];
  list.replaceChildren();
  for (const event of run.doc.events.filter((item) => item.agent === agentId)) {
    const item = el("li", `feed-${event.kind}`);
    const button = el("button");
    button.type = "button";
    button.append(el("time", "", `${event.t.toFixed(1)} s`), el("span", "what", event.text ?? event.kind));
    button.addEventListener("click", () => player.seek(event.t));
    item.append(button);
    const record = event.kind === "decision" ? run.decisions.find((d) => d.record_id === event.ref) : undefined;
    if (record?.decision?.reasons?.length) item.append(reasonList(record.decision.reasons));
    list.append(item);
    items.push({ t: event.t, element: item });
  }
  if (!items.length) list.append(el("li", "iv-empty", "No events for this occupant in the replay."));
  return items;
}

export function openInterview(player: Player, run: Run, agent: number, onClose: () => void) {
  const info = run.doc.agents[agent];
  $("#iv-title").textContent = info.id;
  const persona = info.persona.replace(/_/g, " ");
  $("#iv-who").textContent = `${persona.charAt(0).toUpperCase() + persona.slice(1)}, ${info.model ?? "model not recorded"}${player.runs.length > 1 ? `, ${run.name}` : ""}`;
  $("#iv-answer").replaceChildren();
  const items = drawTimeline(player, run, info.id);
  current = { player, run, agent, onClose, items };
  $("#interview").hidden = false;
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
    item.element.classList.remove("active");
    if (item.t <= t + 1e-6) active = item.element;
  }
  active?.classList.add("active");
}

$("#iv-close").addEventListener("click", () => {
  $("#interview").hidden = true;
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
