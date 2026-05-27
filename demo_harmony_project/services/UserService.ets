import { validateUsername, validatePassword, validatePhone, explainValidation, normalizeInput } from '../utils/Validator'
import { logEvent, logError } from '../utils/Logger'
import { LoginResult, UserProfile, createGuestProfile } from '../models/UserModel'

export class UserService {
  login(username: string, password: string): LoginResult {
    const cleanName = normalizeInput(username)
    if (!validateUsername(cleanName)) {
      logError(explainValidation('username'))
      return { success: false, message: 'invalid username' }
    }
    if (!validatePassword(password)) {
      logError(explainValidation('password'))
      return { success: false, message: 'invalid password' }
    }
    logEvent('login_success')
    return { success: true, message: 'ok', token: 'demo-token' }
  }

  bindPhone(phone: string): boolean {
    const ok = validatePhone(phone)
    if (!ok) {
      logError(explainValidation('phone'))
    }
    return ok
  }

  getProfile(userId: string): UserProfile {
    if (userId.length === 0) {
      return createGuestProfile()
    }
    logEvent('load_profile')
    return { userId: userId, nickname: 'Demo User', phone: '13800000000' }
  }
}
