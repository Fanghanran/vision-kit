import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as camerasApi from '@/api/cameras'
import type { CameraState } from '@/api/types'
import { ElMessage } from 'element-plus'

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

  async function toggleCamera(cameraId: string) {
    try {
      const result = await camerasApi.toggleCamera(cameraId)
      ElMessage.success(`${cameraId} ${result.action === 'started' ? '已启动' : '已停止'}`)
      await fetchCameras()
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '操作失败')
    }
  }

  async function createCamera(payload: camerasApi.CreateCameraPayload) {
    try {
      const result = await camerasApi.createCamera(payload)
      ElMessage.success(`摄像头 ${result.camera_id} 已添加`)
      await fetchCameras()
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '添加失败')
    }
  }

  async function deleteCamera(cameraId: string) {
    try {
      await camerasApi.deleteCamera(cameraId)
      ElMessage.success(`摄像头 ${cameraId} 已删除`)
      await fetchCameras()
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.detail || '删除失败')
    }
  }

  return { cameras, loading, fetchCameras, updateCameraStatus, toggleCamera, createCamera, deleteCamera }
})
