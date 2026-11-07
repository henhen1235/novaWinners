// Types shared by main.ts and the replay, interview and analysis modules.
import type * as THREE from "three";
import type { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { Nav } from "./nav";
import type { ViewMode } from "./floors";

export type SceneDoc = {
  meta?: { name: string; source: string; scale_method: string; generated_at: string };
  walls?: { id: string; level: string; polygon: number[][]; height_m: number }[];
  levels: { id: string; name?: string; elevation_m: number; ceiling_height_m?: number }[];
  rooms: { id: string; level: string; name: string; type?: string; polygon: number[][]; centroid: number[]; area_m2?: number }[];
  doors: { id: string; level: string; segment: number[][]; is_exit: boolean; width_m?: number }[];
  exits: { id: string; level: string; position: number[]; label: string; door_id?: string; leads_to?: string }[];
  warnings: { code: string; message: string; severity: string }[];
  stairs?: { id: string; from_level: string; to_level: string; polygon: number[][]; bottom?: number[]; top?: number[] }[];
  links?: { id: string; type: string; from: { level: string; position: number[] }; to: { level: string; position: number[] }; cost_multiplier?: number }[];
  navmesh: { vertices: number[][]; triangles: number[][]; triangle_level: string[] };
};

export interface ViewerContext {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  doc: SceneDoc;
  nav: Nav;
  level: () => string;
  elevation: () => number;
  token: (name: string) => THREE.Color;
  label: (className: string, build: (element: HTMLDivElement) => void) => CSS2DObject;
  /** Called every animation frame before rendering. */
  onFrame: (fn: (now: number) => void) => void;
  /** Click (not drag) on the stage; return true to stop the route from being drawn. side is 0 or 1 in split view. */
  onPick: (fn: (raycaster: THREE.Raycaster, side: number) => boolean) => void;
  /** Static label groups (rooms, exits, analysis) are mirrored into the right half in split view. */
  addLayer: (key: string, group: THREE.Group) => void;
  /** Split the stage in two: left shows `left`, right shows `right`; rightLabels holds CSS2D labels for the right half. */
  split: (left: THREE.Object3D, right: THREE.Object3D, rightLabels: THREE.Object3D) => void;
  focus: (x: number, z: number) => void;
  /** World position of floor point (x, z) on `level` in the current view (levels move between views). */
  place: (level: string, x: number, z: number, out?: THREE.Vector3) => THREE.Vector3;
  /** Group that holds `level`'s geometry; overlays added here follow the view. */
  levelRoot: (level: string) => THREE.Object3D;
  view: () => ViewMode;
  /** Called after the view, focus floor or exploded spacing changes. */
  onLayout: (fn: (view: ViewMode) => void) => void;
  /** Screen pixels per meter at the camera target; markers scale up when zoomed out. */
  pxPerMeter: () => number;
  /** Live people per floor (null clears the count). */
  setFloorCount: (level: string, people: number | null) => void;
}
