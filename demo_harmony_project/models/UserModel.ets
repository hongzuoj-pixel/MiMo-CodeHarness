export interface LoginResult {
  success: boolean
  message: string
  token?: string
}

export interface UserProfile {
  userId: string
  nickname: string
  phone: string
}

export function createGuestProfile(): UserProfile {
  return { userId: "guest", nickname: "Guest", phone: "" }
}
