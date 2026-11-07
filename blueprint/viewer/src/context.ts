// Types shared by main.ts and the replay, interview and analysis modules.
import type * as THREE from "three";
import type { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { Nav } from "./nav";

export type SceneDoc = {
  meta?: { name: string; source: string; scale_method: string; generated_at: string };
  levels: { id: string; name?: string; elevation_m: number }[];
  walls?: { id: string; level: string; polygon: number[][]; height_m: number }[];
  rooms: { id: string; level: string; name: string; polygon: number[][]; centroid: number[]; area_m2?: number }[];
  doors: { id: string; level: string; segment: number[][]; is_exit: boolean; width_m?: number }[];
  exits: { id: string; level: string; position: number[]; label: string; door_id?: string }[];
  warnings: { code: string; message: string; severity: string }[];
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
}
