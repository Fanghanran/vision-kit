import client from './client'
import type { CameraState } from './types'

export async function getCameras(): Promise<CameraState[]> {
  const { data } = await client.get('/api/cameras')
  return data
}

export async function toggleCamera(cameraId: string): Promise<{ camera_id: string; action: string; status: string }> {
  const { data } = await client.post(`/api/cameras/${cameraId}/toggle`)
  return data
}

export interface CreateCameraPayload {
  id: string
  name: string
  source_type: 'rtsp' | 'video' | 'test'
  rtsp_url?: string
  video_path?: string
  fps?: number
  resolution?: [number, number]
}

export async function createCamera(payload: CreateCameraPayload): Promise<{ camera_id: string; action: string }> {
  const { data } = await client.post('/api/cameras', payload)
  return data
}

export async function deleteCamera(cameraId: string): Promise<{ camera_id: string; action: string }> {
  const { data } = await client.delete(`/api/cameras/${cameraId}`)
  return data
}
