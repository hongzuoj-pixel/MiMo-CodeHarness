import { UserService } from '../services/UserService'
import { logEvent } from '../utils/Logger'
import { showToast } from '../utils/ToastUtil'

const service = new UserService()

export function loadHomeData(userId: string): string {
  const profile = service.getProfile(userId)
  logEvent('home_loaded')
  return showToast(profile.nickname)
}
