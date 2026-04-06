import type { ReviewStepValue } from '../../lib/types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function get<T = unknown>(obj: unknown, path: string, fallback?: T): T {
  const value = path.split('.').reduce<unknown>((current, key) => {
    if (!isRecord(current)) return undefined
    return current[key]
  }, obj)
  return (value ?? fallback) as T
}

export function set(obj: ReviewStepValue | undefined, path: string, value: unknown): ReviewStepValue {
  const result: ReviewStepValue = isRecord(obj) ? { ...obj } : {}
  const keys = path.split('.')
  let cur: ReviewStepValue = result
  for (let i = 0; i < keys.length - 1; i++) {
    const next = cur[keys[i]]
    cur[keys[i]] = isRecord(next) ? { ...next } : {}
    cur = cur[keys[i]] as ReviewStepValue
  }
  cur[keys[keys.length - 1]] = value
  return result
}
