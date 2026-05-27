import { formatError } from './ToastUtil'

export function validateUsername(username: string): boolean {
  return username.trim().length >= 3
}

export function validatePassword(password: string): boolean {
  return password.length >= 6
}

export function validatePhone(phone: string): boolean {
  return /^1[3-9]\d{9}$/.test(phone)
}

export function explainValidation(fieldName: string): string {
  return formatError('Validator', `${fieldName} is invalid`)
}

export const normalizeInput = (value: string): string => {
  return value.trim()
}
