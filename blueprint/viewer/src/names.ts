// Words a facility manager uses, not file names, persona codes or agent ids.

const BUILDINGS: Record<string, string> = {
  tepper_building: "Tepper Quad",
  tepper: "Tepper Quad",
  mudge_house: "Mudge House",
  mesh: "Scanned hall",
  hall: "Scanned hall",
};

export function titleCase(text: string): string {
  return text.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()).trim();
}

export function buildingName(name: string | undefined): string {
  if (!name) return "Building";
  return BUILDINGS[name] ?? titleCase(name);
}

// Replay directory (or scenario file) -> what the run is, in plain words.
const SCENARIOS: Record<string, string> = {
  tepper_fire_noplan: "Weekday fire, no assisted-evacuation plan",
  tepper_fire_plan: "Weekday fire, with the plan",
  tepper_fire: "Weekday fire",
  tepper_alarm: "Fire alarm, no fire",
  loop_tepper_r0_baseline: "AI drill: baseline",
  loop_tepper_r1_attack: "AI drill: attacked",
  loop_tepper_r1_fixed: "AI drill: fixed",
  loop_hall_r0_baseline: "AI drill: baseline",
  loop_hall_r1_attack: "AI drill: attacked",
  loop_hall_r1_fixed: "AI drill: fixed",
  mudge_night_fire: "3 a.m. fire in the common room",
  sim_hall_baseline: "Rule-based occupants",
  sim_hall_cognition: "Occupants who reason",
  hall_classic: "Rule-based occupants",
  hall_llm: "Occupants who reason",
  hall_cast: "Hall with a cast of personas",
  hall_cast_control: "Hall, control cast",
};

export function scenarioName(runDir: string): string {
  return SCENARIOS[runDir] ?? titleCase(runDir);
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

// Regular occupants are residents in a dorm and occupants anywhere else.
let residential = false;
export function setResidential(value: boolean) {
  residential = value;
}

export function personaName(persona: string, count = 1): string {
  if (residential && (persona === "regular" || persona === "regular_employee")) return count === 1 ? "Resident" : "residents";
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
