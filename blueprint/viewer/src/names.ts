// Words a facility manager uses, not file names, persona codes or agent ids.

// Everything here is derived from the scene and replay, so any building reads the same way.
export function titleCase(text: string): string {
  return text.replace(/[_-]+/g, " ").replace(/\s+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()).trim();
}

export function buildingName(name: string | undefined): string {
  return name ? titleCase(name) : "Building";
}

/** A run's title: its own label if the replay has one, else the humanized folder name. */
export function scenarioName(runDir: string, label?: string): string {
  return label?.trim() || titleCase(runDir);
}

const PERSONAS: Record<string, [string, string]> = {
  // persona: [one, many]
  regular: ["Occupant", "occupants"],
  regular_employee: ["Occupant", "occupants"],
  visitor: ["Visitor", "visitors"],
  first_time_visitor: ["Visitor", "visitors"],
  staff: ["Staff", "staff"],
  warden: ["Staff", "staff"],
  older_adult: ["Older adult", "older adults"],
  mobility_impaired: ["Wheelchair user", "wheelchair users"],
  parent: ["Parent", "parents"],
  parent_with_child: ["Parent", "parents"],
  child: ["Child", "children"],
  skeptic: ["Skeptic", "skeptics"],
  committed: ["Busy worker", "busy workers"],
  committed_task: ["Busy worker", "busy workers"],
  fruit_fly: ["Fruit fly", "fruit flies"],
};

export function personaName(persona: string, count = 1): string {
  const [one, many] = PERSONAS[persona] ?? [titleCase(persona), `${titleCase(persona).toLowerCase()}s`];
  return count === 1 ? one : many;
}

/** "Visitor 7": numbered within each persona, in the replay's agent order. */
export function agentNames(agents: { id: string; persona: string }[]): Map<string, string> {
  const seen = new Map<string, number>();
  const names = new Map<string, string>();
  for (const agent of agents) {
    const label = personaName(agent.persona);
    const n = (seen.get(label) ?? 0) + 1;
    seen.set(label, n);
    names.set(agent.id, `${label} ${n}`);
  }
  return names;
}

export function clockText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
