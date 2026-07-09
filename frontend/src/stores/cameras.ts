import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as camerasApi from '@/api/cameras'
import type { CameraState } from '@/api/types'
import type { CameraStats, CameraDetail } from '@/api/cameras'
import { ElMessage } from 'element-plus'

function userMessage(e: any, defaultMsg: string): string {
  const status = e?.response?.status
  const detail = e?.response?.data?.detail || ''
  if (status === 404) return '摄像头不存在，可能已被删除'
  if (status === 409) return '摄像头 ID 已存在'
  return detail || defaultMsg
}

export const useCamerasStore = defineStore('cameras', () => {
  const cameras = ref<CameraState[]>([])
  const loading = ref(false)
  const stats = ref<CameraStats>({ total: 0, online: 0, offline: 0, alerting: 0 })

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

  async function fetchStats() {
    try { stats.value = await camerasApi.getCameraStats() } catch { /* ignore */ }
  }

  async function fetchDetail(cameraId: string): Promise<CameraDetail | null> {
    try { return await camerasApi.getCameraDetail(cameraId) } catch { return null }
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
      await fetchStats()
    } catch (e: any) {
      ElMessage.error(userMessage(e, '操作失败'))
    }
  }

  async function createCamera(payload: camerasApi.CreateCameraPayload) {
    try {
      const result = await camerasApi.createCamera(payload)
      ElMessage.success(`摄像头 ${result.camera_id} 已添加`)
      await fetchCameras()
      await fetchStats()
    } catch (e: any) {
      ElMessage.error(userMessage(e, '添加失败'))
    }
  }

  async function deleteCamera(cameraId: string) {
    try {
      await camerasApi.deleteCamera(cameraId)
      ElMessage.success(`摄像头 ${cameraId} 已删除`)
      await fetchCameras()
      await fetchStats()
    } catch (e: any) {
      ElMessage.error(userMessage(e, '删除失败'))
    }
  }

  async function updateCamera(cameraId: string, payload: Partial<camerasApi.CreateCameraPayload>) {
    try {
      await camerasApi.updateCamera(cameraId, payload)
      ElMessage.success(`摄像头 ${cameraId} 配置已更新`)
      await fetchCameras()
    } catch (e: any) {
      ElMessage.error(userMessage(e, '更新失败'))
    }
  }

  return { cameras, loading, stats, fetchCameras, fetchStats, fetchDetail,
    updateCameraStatus, toggleCamera, createCamera, deleteCamera, updateCamera }
})
