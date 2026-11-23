export interface RobotState {
  connected: boolean;
  mock: boolean;
  tcp_pose: number[];        // [x,y,z,rx,ry,rz]
  joints: number[];          // 6 Gelenkwinkel (rad)
  tcp_force: number[];       // [Fx,Fy,Fz,Tx,Ty,Tz]
  tcp_force_norm: number;    // |F| (N)
  gripper_closed: boolean;
  moving: boolean;
  robot_mode: string | number;
  safety_mode: string | number;
}

export interface Detection {
  class: string;
  confidence: number;
  bbox: number[];            // [x1,y1,x2,y2] normalisiert 0..1
  cx: number;
  cy: number;
  id?: number;
}

export type ControlMode = 'gesture' | 'spacebar' | 'handover';
export type InspectionSource = 'manual' | ControlMode;

export interface HandoverObservation {
  network_camera: boolean;
  hands: Hand[];
  objects: Detection[];
  ready: boolean;
  calibrated: boolean;
  grasp_tcp_m: [number, number, number] | null;
}

export interface Hand {
  handedness: 'Left' | 'Right' | '?';
  score: number;
  landmarks: number[][];     // 21 × [x, y, z], x/y normalisiert 0..1
  gesture: 'open' | 'fist' | 'point' | 'pinch' | 'other';
}

// Virtueller Roboter-Greifer (schematisches Overlay im Bildraum, 0..1).
export interface SimState {
  x: number;
  y: number;
  opening: number;           // 0 geschlossen .. 1 offen
  holding: boolean;          // Teil im Greifer?
  mode: string;              // ready | handover | inspect
  action: string;            // Klartext fürs UI
}

export interface HandoverState {
  active: boolean;           // Hand im Bild (Übergabe möglich)
  progress: number;          // 0..1 bis die Übergabe auslöst
}

export interface Analysis {
  type: 'analysis';
  timestamp: number;
  fps: number;
  hands: Hand[];
  objects: Detection[];
  sim: SimState;
  handover: HandoverState;
}

// Zustand der Roboter-Inspektionssequenz (Server -> Client).
export interface InspectionState {
  type?: 'inspection';
  state: string;             // idle | PICK | INSPECT | READ | SORT | DONE | ERROR
  step: string;
  part_type: string | null;
  progress: number;          // 0..1
  record_id: string | null;
  error: string | null;
  capture_active?: boolean;  // True only after arrival, during capture/OCR.
}

// Ein Eintrag im Inventar / Digital Product Passport.
export interface InventoryRecord {
  id: string;
  seq: number;
  type: string;              // HDD | optical | RAM
  manufacturer: string | null;
  model: string | null;
  capacity: string | null;
  condition: string;
  status: 'identified' | 'review';
  reviewed: boolean;
  ocr_text?: string;
  images?: { front: string | null; back: string | null };
  created_at: number;
  source?: string;
}
