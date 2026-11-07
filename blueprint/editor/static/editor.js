const canvas = document.querySelector("#sheet");
const ctx = canvas.getContext("2d");
const log = document.querySelector("#log");
const planInput = document.querySelector("#plan");
let tool = "add_door";
let image = null;
let pending = null;
const operations = [];

document.querySelectorAll("button[data-op]").forEach((button) => {
  button.addEventListener("click", () => {
    tool = button.dataset.op;
    document.querySelectorAll("button[data-op]").forEach((item) => item.classList.toggle("active", item === button));
  });
});

async function loadPlan() {
  const response = await fetch(`/api/page?path=${encodeURIComponent(planInput.value)}`);
  if (!response.ok) {
    log.textContent = "Could not load that plan.";
    return;
  }
  const blob = await response.blob();
  image = new Image();
  image.onload = draw;
  image.src = URL.createObjectURL(blob);
}

function draw() {
  if (!image) return;
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  ctx.drawImage(image, 0, 0);
  ctx.strokeStyle = "#d7ff4a";
  ctx.lineWidth = 2;
  for (const op of operations) {
    if (op.p1 && op.p2) {
      ctx.beginPath();
      ctx.moveTo(op.p1[0], op.p1[1]);
      ctx.lineTo(op.p2[0], op.p2[1]);
      ctx.stroke();
    } else if (op.point) {
      ctx.fillStyle = "#d7ff4a";
      ctx.fillRect(op.point[0] - 4, op.point[1] - 4, 8, 8);
    }
  }
}

function eventPoint(event) {
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * (canvas.width / rect.width);
  const y = (event.clientY - rect.top) * (canvas.height / rect.height);
  return [Math.round(x), Math.round(y)];
}

canvas.addEventListener("click", (event) => {
  const point = eventPoint(event);
  if (tool === "add_wall_line" || tool === "set_scale") {
    if (!pending) {
      pending = point;
      log.textContent = "Click the other end.";
      return;
    }
    const op = tool === "set_scale"
      ? { op: "set_scale", p1: pending, p2: point, meters: Number(prompt("Meters between the points", "5")) || 5 }
      : { op: "add_wall_line", p1: pending, p2: point, thickness: 0.2 };
    operations.push(op);
    pending = null;
  } else if (tool === "label_room") {
    operations.push({ op: "label_room", point, name: prompt("Room name", "Office") || "Office" });
  } else if (tool === "add_door") {
    operations.push({ op: "add_door", point, width: 0.9 });
  } else {
    operations.push({ op: tool, point });
  }
  draw();
  log.textContent = `${operations.length} operation(s).`;
});

document.querySelector("#rerun").addEventListener("click", async () => {
  log.textContent = "Running…";
  const response = await fetch("/api/rerun", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input: planInput.value,
      output: "out/editor",
      corrections: { operations },
    }),
  });
  const body = await response.json();
  log.textContent = `exit ${body.exit_code}, rooms ${body.rooms}, doors ${body.doors}, exits ${body.exits}\n${
    body.warnings.map((item) => `${item.severity} ${item.code}: ${item.message}`).join("\n")
  }`;
});

loadPlan();
