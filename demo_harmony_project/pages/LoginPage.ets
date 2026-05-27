import { UserService } from '../services/UserService'
import { validateUsername, validatePassword, validatePhone, explainValidation } from '../utils/Validator'
import { showToast } from '../utils/ToastUtil'

const service = new UserService()

export function login(username: string, password: string): string {
  if (!validateUsername(username)) {
    return showToast(explainValidation('username'))
  }
  if (!validatePassword(password)) {
    return showToast(explainValidation('password'))
  }
  const result = service.login(username, password)
  return result.success ? showToast('login success') : showToast(result.message)
}

export function bindPhone(phone: string): string {
  if (!validatePhone(phone)) {
    return showToast(explainValidation('phone'))
  }
  return service.bindPhone(phone) ? showToast('bind success') : showToast('bind failed')
}
