import client from './client'
import type { CameraState } from './types'

export async function getCameras(): Promise<CameraState[]> {
  const { data } = await client.get('/api/cameras')
  return data
}
