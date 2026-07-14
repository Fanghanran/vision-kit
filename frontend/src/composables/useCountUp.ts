/**
 * 数字滚动动画 hook
 *
 * 从旧值平滑滚动到新值，使用 easeOutExpo 缓动
 */
import { ref, watch, type Ref } from 'vue'

export function useCountUp(target: Ref<number>, duration = 800) {
  const current = ref(0)
  let animFrame: number | null = null
  let startTime = 0
  let startVal = 0
  let endVal = 0

  function easeOutExpo(t: number): number {
    return t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
  }

  function animate() {
    const elapsed = Date.now() - startTime
    const progress = Math.min(elapsed / duration, 1)
    current.value = Math.round(startVal + (endVal - startVal) * easeOutExpo(progress))

    if (progress < 1) {
      animFrame = requestAnimationFrame(animate)
    } else {
      current.value = endVal
      animFrame = null
    }
  }

  watch(target, (newVal) => {
    if (animFrame) cancelAnimationFrame(animFrame)
    startVal = current.value
    endVal = newVal
    startTime = Date.now()
    animate()
  }, { immediate: true })

  return current
}
