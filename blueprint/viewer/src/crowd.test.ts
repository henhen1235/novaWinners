// Run: node src/crowd.test.ts
import assert from "node:assert/strict";
import { crowdSpots } from "./crowd.ts";

const grid = (n: number, x0: number, z0: number, step: number, level = "L1") =>
  Array.from({ length: n }, (_, i) => ({ level, x: x0 + (i % 5) * step, z: z0 + Math.floor(i / 5) * step }));

const few = crowdSpots(grid(9, 0, 0, 1));
assert.equal(few.length, 0, "nine people are not a crowd");

const one = crowdSpots(grid(12, 10, 10, 1));
assert.equal(one.length, 1, "twelve people in a tight patch are one crowd");
assert.equal(one[0].n, 12);
assert.ok(Math.abs(one[0].x - 12) < 2 && Math.abs(one[0].z - 11) < 2, "the mark sits on the bunch");

const line = crowdSpots(Array.from({ length: 10 }, (_, i) => ({ level: "L1", x: i * 3, z: 0 })));
assert.equal(line.length, 0, "a spaced-out line is not one section");

const split = crowdSpots([...grid(12, 0, 0, 1), ...grid(11, 40, 0, 1, "L2")]);
assert.equal(split.length, 2, "two floors each get their own mark");
assert.deepEqual(split.map((spot) => spot.level).sort(), ["L1", "L2"]);

console.log("crowd ok");
