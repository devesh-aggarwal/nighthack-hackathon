

export enum ConnectionState {
  DISCONNECTED = 'DISCONNECTED',
  CONNECTING = 'CONNECTING',
  CONNECTED = 'CONNECTED',
  ERROR = 'ERROR',
}

export enum RobotMode {
  IDLE = 'IDLE',
  WALKING = 'WALKING',
  ANIMATING = 'ANIMATING',
}

export interface LogEntry {
  id: string;
  timestamp: number;
  type: 'info' | 'error' | 'function' | 'user' | 'model' | 'event';
  message: string;
}

export type EyeTrackingSource = 'mouse' | 'server' | 'mediapipe';
export type CameraSource = 'browser' | 'server';

export interface AppConfig {
  modelName: string;
  ttsModelName?: string;
  voiceName: string;
  extraInstructions: string; // added after the robot's prompt (lab/prompt.md)
  enableVideo: boolean;
  enableAudio: boolean;
  robotApiUrl: string; // URL for the FastAPI bridge
  eyeTrackingSource: EyeTrackingSource;
  cameraSource: CameraSource;
  enableAffectiveDialog: boolean;
  enableProactiveAudio: boolean;
  // Accessibility features (see README)
  captions: boolean; // show what Pupper says as text on the face
}

/** Big words on Pupper's screen: a sound alert. */
export interface ScreenDisplay {
  text: string;
  detail: string;
  color: string;
  seconds: number;
  kind?: string; // "sound" or "emergency" takes over the whole screen
  flash?: boolean; // emergency: the red flashes on and off
  siren?: boolean; // emergency: the app sounds its siren (services/siren.ts)
  say?: string; // spoken out loud once, when the event arrives (services/speak.ts)
}

/** Something that happened on the robot (GET /events): a sound, or someone collapsed. */
export interface RobotEvent {
  id: number;
  time: number;
  clock: string;
  kind: string;
  text: string;
  for_gemini: string;
  display: ScreenDisplay | null;
  data: Record<string, unknown>;
}

export interface MoveCommand {
  vx: number; // forward/back
  vy: number; // left/right
  wz: number; // turn
  duration: number;
}

export interface SystemStatus {
  battery: {
    percentage: number | null;
    is_charging: boolean;
    voltage: number | null;
  };
  cpu: {
    usage: number;
    temperature: number | null;
  };
  services: {
    robot: 'active' | 'inactive' | 'unknown';
    internet: 'online' | 'offline' | 'unknown';
  };
}