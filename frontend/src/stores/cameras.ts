import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as camerasApi from '@/api/cameras'
import type { CameraState } from '@/api/types'

export const useCamerasStore = defineStore('cameras', () => {
  const cameras = ref<CameraState[]>([])
  const loading = ref(false)

  async function fetchCameras() {
    loading.value = true
    try {
      cameras.value = await camerasApi.getCameras()
    } catch (e) {
      console.error('fetchCameras failed:', e)
    } finally {
      loading.value = false
    }
  }

  function updateCameraStatus(cameraId: string, status: string) {
    const cam = cameras.value.find((c) => c.camera_id === cameraId)
    if (cam) cam.status = status
  }

  return { cameras, loading, fetchCameras, updateCameraStatus }
})
