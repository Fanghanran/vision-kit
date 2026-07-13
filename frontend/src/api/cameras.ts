import client from './client'
import type { CameraState } from './types'

// ─── 基础 API ─────────────────────────────────────────────────

export async function getCameras(): Promise<CameraState[]> {
  const { data } = await client.get('/api/cameras')
  return data
}

export async function toggleCamera(cameraId: string): Promise<{ camera_id: string; action: string; status: string }> {
  const { data } = await client.post(`/api/cameras/${cameraId}/toggle`)
  return data
}

export interface DetectorOverride {
  model_path?: string
  confidence?: number
  iou_threshold?: number
  classes?: number[] | null
  input_size?: number
}

export interface CreateCameraPayload {
  id: string
  name: string
  source_type: 'rtsp' | 'video' | 'test'
  rtsp_url?: string
  video_path?: string
  fps?: number
  resolution?: [number, number]
  detector?: DetectorOverride
}

export async function createCamera(payload: CreateCameraPayload): Promise<{ camera_id: string; action: string }> {
  const { data } = await client.post('/api/cameras', payload)
  return data
}

export async function deleteCamera(cameraId: string): Promise<{ camera_id: string; action: string }> {
  const { data } = await client.delete(`/api/cameras/${cameraId}`)
  return data
}

export async function updateCamera(cameraId: string, payload: Partial<CreateCameraPayload>): Promise<{ camera_id: string; action: string }> {
  const { data } = await client.put(`/api/cameras/${cameraId}`, payload)
  return data
}

// ─── 新增 v2 API ─────────────────────────────────────────────

export interface CameraStats {
  total: number
  online: number
  offline: number
  alerting: number
}

export async function getCameraStats(): Promise<CameraStats> {
  const { data } = await client.get('/api/cameras/stats')
  return data
}

export interface CameraDetail {
  camera_id: string
  camera_name: string
  status: string
  source_type: string
  fps: number
  queue_size: number
  total_detections: number
  total_alerts: number
  uptime_seconds: number
  error_message: string
  rtsp_url: string
  resolution: [number, number]
}

export async function getCameraDetail(cameraId: string): Promise<CameraDetail> {
  const { data } = await client.get(`/api/cameras/${cameraId}`)
  return data
}
